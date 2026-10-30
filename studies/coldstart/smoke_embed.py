"""G3b (Spec 14 §6): o servidor de embedding do bootstrap (Qwen3-Embedding-4B, pooling, matryoshka) sobe e devolve 768 dimensoes?

Mesmo comando do remote/bootstrap.sh, em 1 GPU (a convivencia com a LLM em 2 GPUs nao cabe numa maquina barata); o log do servidor volta no resultado.

(texto do smoke original) a imagem enxuta consegue SUBIR o vLLM numa 3090 de verdade?

O teste de importacao no CI nao prova isso (o flashinfer so procura kernels ao carregar o modelo). Aqui: uma 3090 barata, 1 GPU,
a imagem enxuta, um modelo minusculo (Qwen3-0.6B) e uma chamada de chat. Mede pull ate `running`, tempo do vLLM ate ficar saudavel
e o que o log disser do flashinfer. Destroi no `finally`. Custo esperado: poucos centavos.

  VAST_API_KEY=... SSH_KEY=<chave privada cadastrada na conta> python smoke_vllm.py <imagem> --out results/smoke.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "queue"))
from app.gpu.vast import RealVast  # noqa: E402

NL = chr(10)


def ssh(host: str, port: int, key: str, cmd: str, timeout: int = 120) -> str:
    r = subprocess.run(["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15", "-p", str(port), f"root@{host}", cmd], capture_output=True, timeout=timeout)
    return (r.stdout + r.stderr).decode(errors="replace")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    key = os.environ["SSH_KEY"]
    v = RealVast(os.environ["VAST_API_KEY"], "https://console.vast.ai/api", "RTX 3090", 1)
    credit0 = await v.credit()
    rec: dict = {"image": a.image, "credit_start": credit0}
    q = {"verified": {"in": [True, False]}, "external": {"in": [True, False]}, "rentable": {"eq": True}, "type": "on-demand", "limit": 60,
         "order": [["dph_total", "asc"]], "num_gpus": {"eq": 1}, "gpu_name": {"in": ["RTX 3090"]}, "cuda_max_good": {"gte": 13.0},
         "inet_down": {"gte": 500}, "reliability2": {"gte": 0.95}, "disk_space": {"gte": 50}}
    offers = [o for o in v._body(await v._req("POST", "/v0/bundles/", json=q)).get("offers", []) if not str(o.get("geolocation") or "").endswith((", CN", ", CZ"))]  # noqa: SLF001
    print(len(offers), "ofertas 1x3090", flush=True)
    iid = None
    try:
        for o in offers[:4]:
            t0 = time.time()
            res = await v.create_instance(int(o["id"]), a.image, 50, f"smoke-{int(t0)}", "#!/bin/bash" + NL + "sleep 3600" + NL, {})
            if not res.ok:
                print("criar falhou:", res.error, res.msg, flush=True)
                continue
            iid = res.instance_id
            inst = None
            while time.time() - t0 < 900:
                inst = await v.get_instance(iid)
                if inst and inst.running:
                    break
                await asyncio.sleep(5)
            rec.update({"host": o["machine_id"], "geo": o.get("geolocation"), "dph": o.get("dph_total"), "running_s": round(time.time() - t0, 1)})
            if not (inst and inst.running):
                rec["result"] = "nao_chegou_a_running"
                await v.destroy(iid)
                iid = None
                continue
            break
        if not iid:
            rec["result"] = rec.get("result") or "sem_instancia"
            return
        print(f"running em {rec['running_s']}s; subindo o vLLM", flush=True)
        for _ in range(12):  # sshd pode demorar alguns segundos depois do running
            out = ssh(inst.ssh_host, int(inst.ssh_port), key, "echo ok")
            if out.strip().endswith("ok"):
                break
            time.sleep(5)
        script = (". /venv/main/bin/activate; mkdir -p /workspace; cd /workspace; "
                  "if vllm serve --help=all 2>/dev/null | grep -q -- '--runner'; then RUN='--runner pooling --convert embed'; else RUN='--task embed'; fi; "
                  "nohup vllm serve Qwen/Qwen3-Embedding-4B $RUN --served-model-name qwen-embedding --host 127.0.0.1 --port 18001 "
                  "--gpu-memory-utilization 0.5 --max-model-len 8192 --max-num-seqs 64 --max-num-batched-tokens 32768 "
                  "--hf-overrides '{\"is_matryoshka\":true}' > /workspace/vllm_embed.log 2>&1 & echo iniciado")
        print(ssh(inst.ssh_host, int(inst.ssh_port), key, script).strip(), flush=True)
        t1 = time.time()
        healthy = False
        while time.time() - t1 < 420:
            h = ssh(inst.ssh_host, int(inst.ssh_port), key, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18001/health")
            if h.strip().endswith("200"):
                healthy = True
                break
            time.sleep(8)
        rec["vllm_health_s"] = round(time.time() - t1, 1) if healthy else None
        rec["healthy"] = healthy
        if healthy:
            body = json.dumps({"model": "qwen-embedding", "input": ["Contratacao de merenda escolar", "Receita de bolo"], "dimensions": 768})
            out = ssh(inst.ssh_host, int(inst.ssh_port), key, "curl -s http://127.0.0.1:18001/v1/embeddings -H 'Content-Type: application/json' -d '" + body + "'")
            rec["chat_ok"] = '"embedding"' in out
            try:
                rec["dims"] = len(json.loads(out)["data"][0]["embedding"])
            except Exception:  # noqa: BLE001
                rec["resp"] = out[:300]
        log = ssh(inst.ssh_host, int(inst.ssh_port), key, "grep -i -E 'error|Traceback|Exception|matryoshka|pooling|Application startup|memory' /workspace/vllm_embed.log | head -25; tail -5 /workspace/vllm_embed.log | cut -c1-200")
        rec["log_flashinfer"] = log[-1800:]
        rec["result"] = "ok" if rec.get("chat_ok") else "vllm_falhou"
    finally:
        if iid:
            await v.destroy(iid)
        rec["credit_end"] = await v.credit()
        rec["spent_usd"] = round(credit0 - rec["credit_end"], 3)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, indent=2, ensure_ascii=False)[:1500], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
