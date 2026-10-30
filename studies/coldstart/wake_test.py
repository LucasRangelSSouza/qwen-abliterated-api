"""G2/G3 do estudo de cold start (Spec 14 §5c/§5d): UMA acordada de 2x3090 pela cadeia real de producao, testando tudo junto.

Roda, nesta ordem, com a GPU desligada no inicio:
  1. ajusta o env do llm-queue na VPS (imagem, origem dos pesos, embedding) e recria o contêiner;
  2. no mesmo instante, um pedido em stream ao llm-access (mede aviso de espera, nº de avisos, tempo ate o 1º token real) e
     uma run do Hermes pelo kernel-harness (confere o evento `thinking` do vigia da fila e que o timeout nao derruba);
  3. com a GPU pronta: embedding (768 dimensoes, vazao, convivencia com a LLM) e limites reais (chaves de teste T1/T2/T3);
  4. le as etapas do cold start (imagem, download, vLLM) do log de ciclos do painel;
  5. opcional: envia o cache de compilacao do vLLM ao Drive; mede o religar depois de parar;
  6. para e destroi a instancia (nada fica ligado) e grava o resultado em JSON.
Guardas: saldo minimo antes de comecar, teto de tempo da acordada e destruicao no `finally`. Segredos vem do cofre local.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

VAULT = Path(os.environ.get("VAULT_DIR", r"C:\Users\lucas.rangel\Desktop\claude\personal-skills\secrets"))
HOST = os.environ.get("QWEN_HOST", "https://qwen.rangeltech.net")
VPS = ["ssh", "-i", os.environ.get("VPS_KEY", "/tmp/vk"), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
       "-o", "BatchMode=yes", "-o", "ServerAliveInterval=20", "deploy@66.94.101.153"]
NL = chr(10)
T0 = time.time()
OUT: dict = {"events": []}


def env_file(name: str) -> dict:
    d = {}
    for ln in (VAULT / name).read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            d[k] = v
    return d


ACC, GPUENV, QENV = env_file("llm-access.env"), env_file("qwen-gpu.env"), env_file("llm-queue.env")
OWNER = ACC["ACCESS_OWNER_KEY"]


def log(msg: str) -> None:
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    OUT["events"].append(line)


def vps(script: str, timeout: int = 300) -> str:
    r = subprocess.run(VPS + ["sudo -n bash -s"], input=script.replace("\r\n", NL).encode(), capture_output=True, timeout=timeout)
    return r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")


def set_queue_env(changes: dict[str, str | None]) -> None:
    """Ajusta chaves de /opt/platform/secrets/llm-queue.env (valor None apaga) e recria o contêiner do llm-queue."""
    lines = []
    for k, v in changes.items():
        lines.append(f"sed -i '/^{k}=/d' /opt/platform/secrets/llm-queue.env")
        if v is not None:
            lines.append(f"printf '%s{NL}' '{k}={v}' >> /opt/platform/secrets/llm-queue.env")
    script = NL.join(lines + [
        "cd /opt/platform/stacks/llm-queue",
        "docker compose -p llm-queue -f compose.yml --env-file .release.env up -d --force-recreate --no-deps llm-queue 2>&1 | tail -2",
        "for i in $(seq 1 40); do s=$(docker inspect --format '{{.State.Health.Status}}' llm-queue 2>/dev/null); [ \"$s\" = healthy ] && break; sleep 3; done; echo health=$s", ""])
    log("env do llm-queue: " + ", ".join(f"{k}={'***' if 'RCLONE' in k else v}" for k, v in changes.items()) + " -> " + vps(script).strip().splitlines()[-1])


def admin_client() -> httpx.Client:
    c = httpx.Client(base_url=HOST, timeout=60)
    r = c.post("/admin/api/login", json={"password": ACC["ACCESS_ADMIN_PASSWORD"]})
    r.raise_for_status()
    return c


def stream_probe(res: dict) -> None:
    """Pedido em stream pelo llm-access com a GPU desligada: cronometra o aviso e o primeiro dado real."""
    t = time.time()
    body = {"model": "qwen-abliterated", "stream": True, "max_tokens": 24, "messages": [{"role": "user", "content": "Diga apenas: pronto."}]}
    notices, first_notice, first_real, last = 0, None, None, None
    try:
        with httpx.stream("POST", HOST + "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {OWNER}"},
                          timeout=httpx.Timeout(2000.0, connect=20.0)) as r:
            res["http"] = r.status_code
            res["headers_s"] = round(time.time() - t, 2)
            for line in r.iter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                try:
                    j = json.loads(line[6:])
                except ValueError:
                    continue
                if "x_queue" in j:
                    notices += 1
                    first_notice = first_notice or round(time.time() - t, 2)
                    last = j["x_queue"]
                    continue
                d = (j.get("choices") or [{}])[0].get("delta") or {}
                if first_real is None and (d.get("content") or d.get("reasoning") or d.get("reasoning_content")):
                    first_real = round(time.time() - t, 2)
    except Exception as e:  # noqa: BLE001
        res["error"] = repr(e)
    res.update({"notices": notices, "first_notice_s": first_notice, "first_real_token_s": first_real, "last_xq": last, "total_s": round(time.time() - t, 1)})


def hermes_run(res: dict) -> None:
    here = Path(__file__).parent
    t = time.time()
    r = subprocess.run([sys.executable, str(here / "hermes_run.py"), "Responda somente: HERMES QWEN OK"], capture_output=True, timeout=1900)
    out = r.stdout.decode(errors="replace")
    res["total_s"] = round(time.time() - t, 1)
    res["thinking_events"] = [ln.strip()[:200] for ln in out.splitlines() if " thinking:" in ln][:6]
    res["completed"] = "HERMES QWEN OK" in out
    res["tail"] = out[-600:]


def embed_tests(res: dict) -> None:
    h = {"Authorization": f"Bearer {OWNER}"}
    frases = ["Contratacao de servicos de merenda escolar para a rede municipal", "Aquisicao de material didatico para o ensino fundamental",
              "Pregao eletronico para transporte escolar rural", "Receita de bolo de cenoura com cobertura de chocolate"]
    r = httpx.post(HOST + "/v1/embeddings", headers=h, json={"model": "qwen-embedding", "input": frases, "dimensions": 768}, timeout=300)
    res["http"] = r.status_code
    if r.status_code != 200:
        res["body"] = r.text[:300]
        return
    v = [x["embedding"] for x in r.json()["data"]]
    res["dims"] = len(v[0])

    def cos(a, b):
        na, nb = sum(x * x for x in a) ** 0.5, sum(x * x for x in b) ** 0.5
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    res["cos_merenda_vs_didatico"], res["cos_merenda_vs_bolo"] = round(cos(v[0], v[1]), 3), round(cos(v[0], v[3]), 3)
    texts = [f"Edital {i}: contratacao de servicos e aquisicao de materiais para a rede publica de ensino, lote {i % 9}" for i in range(256)]
    t = time.time()
    done = []

    def worker(chunk):
        rr = httpx.post(HOST + "/v1/embeddings", headers=h, json={"model": "qwen-embedding", "input": chunk, "dimensions": 768}, timeout=600)
        done.append(rr.status_code)
    ths = [threading.Thread(target=worker, args=(texts[i:i + 32],)) for i in range(0, 256, 32)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    dt = time.time() - t
    res["throughput_texts_s"], res["batches_ok"] = round(256 / dt, 1), sum(1 for c in done if c == 200)


def limit_tests(res: dict) -> None:
    c = admin_client()

    def person(name, sess, week):
        r = c.post("/admin/api/people", json={"name": name, "note": "QA Spec 14 (pode suspender)", "session_limit_tokens": sess, "week_limit_tokens": week})
        r.raise_for_status()
        j = r.json()
        return j["person"]["id"], j["api_key"]

    def chat(key, mt=8):
        return httpx.post(HOST + "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=300,
                          json={"model": "qwen-abliterated", "max_tokens": mt, "stream": False, "messages": [{"role": "user", "content": "Diga oi."}]})
    ids = {}
    t1, k1 = person("QA T1 sem limite", None, None)
    r = chat(k1)
    res["T1"] = {"http": r.status_code}
    t2, k2 = person("QA T2 so sessao 100", 100, None)
    codes = []
    for _ in range(12):
        r = chat(k2)
        codes.append(r.status_code)
        if r.status_code == 429:
            res["T2_429"] = {"body": r.json().get("error"), "retry_after": r.headers.get("retry-after")}
            break
    res["T2"] = {"codes": codes}
    t3, k3 = person("QA T3 so semanal 200", None, 200)
    codes = []
    for _ in range(20):
        r = chat(k3)
        codes.append(r.status_code)
        if r.status_code == 429:
            res["T3_429"] = {"body": r.json().get("error"), "retry_after": r.headers.get("retry-after")}
            break
    res["T3"] = {"codes": codes}
    u = httpx.get(HOST + "/v1/usage", headers={"Authorization": f"Bearer {k2}"}).json()
    res["T2_usage"] = u
    r = c.post(f"/admin/api/people/{t1}/suspend")
    res["T5_suspend"] = {"http": r.status_code, "chat_after": chat(k1).status_code}
    for pid in (t1, t2, t3):
        c.post(f"/admin/api/people/{pid}/suspend")
    ids.update({"t1": t1, "t2": t2, "t3": t3})
    res["person_ids"] = ids


def machines(c: httpx.Client) -> dict:
    return c.get("/admin/api/machines?limit=5").json()


def vast_credit() -> float:
    k = env_file("qwen-api.env")["VAST_API_KEY"]
    return float(httpx.get("https://console.vast.ai/api/v0/users/current/", headers={"Authorization": f"Bearer {k}"}, timeout=30).json().get("credit") or 0)


def cleanup() -> None:
    try:
        httpx.post("http://llm-queue.rt:8000/admin/gpu/destroy", timeout=1)  # so funciona na rede interna; fallback abaixo
    except Exception:  # noqa: BLE001
        pass
    out = vps(f"curl -s -X POST -H 'X-Admin-Key: {QENV['LLMQ_ADMIN_KEY']}' http://llm-queue.rt:8000/admin/gpu/destroy; "
              "docker exec llm-access python -c \"print(1)\" >/dev/null 2>&1; echo")
    log("destroy: " + out.strip()[:120])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--env", default="{}", help="JSON com ajustes do env do llm-queue (null apaga)")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--limits", action="store_true")
    ap.add_argument("--hermes", action="store_true")
    ap.add_argument("--restart-test", action="store_true")
    ap.add_argument("--cache-up", action="store_true", help="envia o cache de compilacao do vLLM ao Drive no fim")
    ap.add_argument("--max-min", type=float, default=38.0, help="teto de minutos da acordada")
    ap.add_argument("--min-credit", type=float, default=0.80)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    credit0 = vast_credit()
    log(f"saldo inicial US${credit0:.3f}")
    OUT.update({"label": a.label, "credit_start": credit0})
    if credit0 - 0.50 < a.min_credit:
        log("saldo insuficiente para esta acordada: nao roda")
        OUT["skipped"] = "saldo"
        Path(a.out).write_text(json.dumps(OUT, indent=2), encoding="utf-8")
        return
    deadline = time.time() + a.max_min * 60
    try:
        destroy_first = vps(f"curl -s -X POST -H 'X-Admin-Key: {QENV['LLMQ_ADMIN_KEY']}' http://llm-queue.rt:8000/admin/gpu/destroy")
        log("destroy previo: " + destroy_first.strip()[:100])
        set_queue_env(json.loads(a.env))
        c = admin_client()
        OUT["machines_before"] = machines(c)
        res_stream, res_h = {}, {}
        th = [threading.Thread(target=stream_probe, args=(res_stream,))]
        if a.hermes:
            th.append(threading.Thread(target=hermes_run, args=(res_h,)))
        log("pedidos com a GPU desligada")
        [t.start() for t in th]
        while any(t.is_alive() for t in th) and time.time() < deadline:
            time.sleep(5)
        OUT["stream"], OUT["hermes"] = res_stream, res_h
        log(f"stream: {json.dumps(res_stream)[:300]}")
        if a.hermes:
            log(f"hermes: completed={res_h.get('completed')} total={res_h.get('total_s')}s thinking={res_h.get('thinking_events')}")
        if time.time() >= deadline:
            raise TimeoutError("teto de tempo da acordada")
        OUT["cycle"] = machines(c)["cycles"][:1]
        log("ciclo: " + json.dumps(OUT["cycle"])[:400])
        if a.embed:
            r = {}
            embed_tests(r)
            OUT["embed"] = r
            log("embed: " + json.dumps(r)[:300])
        if a.limits:
            r = {}
            limit_tests(r)
            OUT["limits"] = r
            log("limites: " + json.dumps(r)[:500])
        if a.cache_up:
            log("cache de compilacao: " + vps("echo (nao implementado nesta versao)").strip()[:80])
        if a.restart_test:
            vps(f"curl -s -X POST -H 'X-Admin-Key: {QENV['LLMQ_ADMIN_KEY']}' http://llm-queue.rt:8000/admin/gpu/stop")
            time.sleep(10)
            t = time.time()
            rs: dict = {}
            stream_probe(rs)
            OUT["restart"] = {"total_s": round(time.time() - t, 1), **rs}
            log("religar: " + json.dumps(OUT["restart"])[:300])
    except Exception as e:  # noqa: BLE001
        OUT["error"] = repr(e)
        log(f"ERRO: {e!r}")
    finally:
        cleanup()
        time.sleep(5)
        OUT["credit_end"] = vast_credit()
        OUT["spent_usd"] = round(credit0 - OUT["credit_end"], 3)
        log(f"saldo final US${OUT['credit_end']:.3f} (gasto US${OUT['spent_usd']:.3f})")
        Path(a.out).write_text(json.dumps(OUT, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
