"""G1b do estudo de cold start (Spec 14 §5c): a imagem enxuta consegue SUBIR o vLLM numa 3090 de verdade?

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
    offers = [o for o in v._body(await v._req("POST", "/v0/bundles/", json=q)).get("offers", []) if not str(o.get("geolocation") or "").endswith(", CN")]  # noqa: SLF001
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
        script = (". /venv/main/bin/activate; export HF_HUB_ENABLE_HF_TRANSFER=0; mkdir -p /workspace; cd /workspace; "
                  "nohup vllm serve Qwen/Qwen3-0.6B --host 127.0.0.1 --port 18000 --max-model-len 4096 --gpu-memory-utilization 0.6 > /workspace/vllm_smoke.log 2>&1 & echo iniciado")
        print(ssh(inst.ssh_host, int(inst.ssh_port), key, script).strip(), flush=True)
        t1 = time.time()
        healthy = False
        while time.time() - t1 < 420:
            h = ssh(inst.ssh_host, int(inst.ssh_port), key, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/health")
            if h.strip().endswith("200"):
                healthy = True
                break
            time.sleep(8)
        rec["vllm_health_s"] = round(time.time() - t1, 1) if healthy else None
        rec["healthy"] = healthy
        if healthy:
            body = json.dumps({"model": "Qwen/Qwen3-0.6B", "messages": [{"role": "user", "content": "Diga oi."}], "max_tokens": 16})
            out = ssh(inst.ssh_host, int(inst.ssh_port), key, "curl -s http://127.0.0.1:18000/v1/chat/completions -H 'Content-Type: application/json' -d '" + body + "'")
            rec["chat_ok"] = '"choices"' in out
        log = ssh(inst.ssh_host, int(inst.ssh_port), key, "grep -i -E 'flashinfer|cubin|error|Traceback|backend' /workspace/vllm_smoke.log | head -25")
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
