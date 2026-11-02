"""Bateria de capacidade do vLLM (Spec 15 §3.4). Roda DENTRO da GPU contra 127.0.0.1:18000. So le metricas; nao mexe na instancia.
Uso: python capacity.py <saida.jsonl> [plano]   plano: short | long | all (padrao all)
"""
import json, random, re, subprocess, sys, threading, time, urllib.request, uuid

URL = "http://127.0.0.1:18000"
OUT = sys.argv[1]
PLAN = sys.argv[2] if len(sys.argv) > 2 else "all"
WORDS = ("motor oleo filtro correia vela bobina injetor sensor lambda catalisador embreagem cambio freio pastilha disco amortecedor "
         "suspensao bateria alternador radiador ventoinha termostato bomba coletor escapamento turbina intercooler pneu alinhamento "
         "balanceamento diagnostico scanner codigo falha torque pressao temperatura consumo combustivel etanol gasolina diesel").split()


def filler(n_words, seed):
    rnd = random.Random(seed)
    return " ".join(rnd.choice(WORDS) for _ in range(n_words))


def prompt_for(ptoks):
    # ~1.5 tokens por palavra em portugues tecnico; id unico no inicio evita acerto de cache de prefixo
    uid = uuid.uuid4().hex
    if ptoks <= 200:
        return f"[{uid}] Em ate 150 palavras: explique como diagnosticar um carro que falha em marcha lenta e esquenta o motor."
    body = filler(int(ptoks / 1.5), uid)
    return f"[{uid}] Resuma em 3 frases o texto tecnico abaixo.\n\n{body}"


def sample_gpu():
    q = "power.draw,memory.used,utilization.gpu,temperature.gpu"
    out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip().splitlines()
    rows = [[float(x) for x in ln.split(",")] for ln in out if ln.strip()]
    return rows


def sample_metrics():
    try:
        txt = urllib.request.urlopen(URL + "/metrics", timeout=5).read().decode()
    except Exception:
        return {}
    m = {}
    for ln in txt.splitlines():
        if ln.startswith("#"):
            continue
        for key in ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc", "gpu_cache_usage_perc"):
            if f"vllm:{key}" in ln or f":{key}{{" in ln:
                try:
                    m[key] = float(ln.rsplit(" ", 1)[1])
                except ValueError:
                    pass
    return m


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = False
        self.rows = []

    def run(self):
        while not self.stop:
            try:
                g = sample_gpu()
                mm = sample_metrics()
                self.rows.append({"power": sum(r[0] for r in g), "mem_max": max(r[1] for r in g), "util": sum(r[2] for r in g) / len(g),
                                  "temp": max(r[3] for r in g), **mm})
            except Exception:
                pass
            time.sleep(4)


def one_request(ptoks, max_tokens, res):
    body = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": prompt_for(ptoks)}], "max_tokens": max_tokens,
            "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    comp = prompt = 0
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                ln = raw.decode().strip()
                if not ln.startswith("data:") or ln.endswith("[DONE]"):
                    continue
                d = json.loads(ln[5:])
                if d.get("usage"):
                    comp = d["usage"]["completion_tokens"]
                    prompt = d["usage"]["prompt_tokens"]
                ch = d.get("choices") or []
                if ch and first is None and (ch[0].get("delta", {}).get("content") or ch[0].get("delta", {}).get("reasoning")):
                    first = time.time() - t0
        total = time.time() - t0
        res.append({"ok": True, "ttft": first, "total": total, "comp": comp, "prompt": prompt,
                    "tps": comp / max(total - (first or 0), 1e-6) if comp > 1 else 0.0})
    except Exception as e:  # noqa: BLE001
        res.append({"ok": False, "err": str(e)[:120], "total": time.time() - t0})


def pct(a, p):
    if not a:
        return None
    a = sorted(a)
    return a[min(len(a) - 1, int(round(p / 100 * (len(a) - 1))))]


def run_step(label, n, ptoks, secs, max_tokens=256):
    res = []
    smp = Sampler()
    smp.start()
    deadline = time.time() + secs

    def user():
        while time.time() < deadline:
            one_request(ptoks, max_tokens, res)

    th = [threading.Thread(target=user) for _ in range(n)]
    t0 = time.time()
    [t.start() for t in th]
    [t.join() for t in th]
    wall = time.time() - t0
    smp.stop = True
    ok = [r for r in res if r["ok"]]
    ttfts = [r["ttft"] for r in ok if r["ttft"] is not None]
    tps = [r["tps"] for r in ok if r["tps"] > 0]
    agg = sum(r["comp"] for r in ok) / wall
    s = smp.rows
    row = {"label": label, "users": n, "prompt_toks_target": ptoks, "secs": round(wall, 1), "reqs": len(res), "errors": len(res) - len(ok),
           "ttft_p50": pct(ttfts, 50), "ttft_p95": pct(ttfts, 95), "ttft_max": max(ttfts) if ttfts else None,
           "tps_user_p50": pct(tps, 50), "tps_user_min": min(tps) if tps else None, "agg_tps": round(agg, 1),
           "prompt_avg": round(sum(r["prompt"] for r in ok) / len(ok)) if ok else None,
           "gpu_power_w_avg": round(sum(x["power"] for x in s) / len(s), 1) if s else None,
           "gpu_power_w_max": round(max((x["power"] for x in s), default=0), 1),
           "mem_mib_max": max((x["mem_max"] for x in s), default=None), "util_avg": round(sum(x["util"] for x in s) / len(s), 1) if s else None,
           "temp_max": max((x["temp"] for x in s), default=None),
           "kv_usage_max": max((x.get("kv_cache_usage_perc", x.get("gpu_cache_usage_perc", 0)) for x in s), default=None),
           "running_max": max((x.get("num_requests_running", 0) for x in s), default=None),
           "waiting_max": max((x.get("num_requests_waiting", 0) for x in s), default=None)}
    row["sla_ok"] = bool(ttfts and row["ttft_p95"] is not None and row["ttft_p95"] <= 3.0 and row["tps_user_p50"] and row["tps_user_p50"] >= 20 and not row["errors"]) if ptoks <= 200 else None
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


# aquecimento (compila grafos do tamanho certo)
run_step("warmup", 2, 100, 15)
if PLAN in ("short", "all"):  # noqa
    for n in (1, 2, 4, 8, 12, 16, 20, 24, 32, 40):
        run_step("curto", n, 100, 60)
if PLAN in ("long", "all"):
    for n in (1, 4, 8, 16):
        run_step("8k", n, 8000, 60)
    for n in (1, 2, 4, 8):
        run_step("32k", n, 32000, 90)
    for n in (1, 2):
        run_step("60k", n, 60000, 120, max_tokens=128)
if PLAN == "c":
    for n in (24, 32, 48, 64):
        run_step("curto", n, 100, 60)
    for n in (1, 4, 8):
        run_step("8k", n, 8000, 60)
    for n in (1, 2):
        run_step("48k", n, 48000, 150, max_tokens=128)
if PLAN == "r":
    for n in (1, 8, 16):
        run_step("curto", n, 100, 45)
    for n in (1, 2):
        run_step("8k", n, 8000, 45)
    run_step("24k", 1, 24000, 90, max_tokens=128)
print("FIM", flush=True)
