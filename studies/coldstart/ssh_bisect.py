"""Diagnostico (Spec 14, G2): por que o SSH da fila foi recusado ("Permission denied (publickey)") na imagem enxuta?

Cria 3 instancias baratas de 1 GPU ao mesmo tempo, com a imagem enxuta, variando so o que a fila manda:
  A) env e onstart reais da fila (render_env + bootstrap.sh)   B) so o env real, onstart `sleep`   C) so o onstart real, env vazio
e tenta abrir SSH com a chave da fila em cada uma. Destroi tudo no fim. Custo esperado: centavos.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "queue"))
from app.config import Settings  # noqa: E402
from app.gpu.controller import load_onstart, render_env  # noqa: E402
from app.gpu.vast import RealVast  # noqa: E402

NL = chr(10)
IMAGE = sys.argv[1]
KEY = os.environ["SSH_KEY"]


def try_ssh(host, port) -> str:
    r = subprocess.run(["ssh", "-i", KEY, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        "-o", "ConnectTimeout=10", "-p", str(port), f"root@{host}", "echo SSH-OK; ls /workspace 2>&1 | head -3; tail -5 /workspace/llmq-bootstrap.log 2>&1"], capture_output=True, timeout=60)
    return (r.stdout + r.stderr).decode(errors="replace")[-400:]


async def one(v, offer, label, onstart, env):
    t0 = time.time()
    res = await v.create_instance(int(offer["id"]), IMAGE, 70, f"bisect-{label}-{int(t0)}", onstart, env)
    if not res.ok:
        return {"label": label, "result": "criar_falhou", "detail": f"{res.error} {res.msg}"[:150]}
    iid = res.instance_id
    out = {"label": label, "host": offer["machine_id"], "geo": offer.get("geolocation")}
    try:
        inst = None
        while time.time() - t0 < 900:
            inst = await v.get_instance(iid)
            if inst and inst.running:
                break
            await asyncio.sleep(5)
        if not (inst and inst.running):
            out["result"] = "nao_chegou_a_running"
            return out
        out["running_s"] = round(time.time() - t0, 1)
        for k in range(24):
            msg = try_ssh(inst.ssh_host, int(inst.ssh_port))
            if "SSH-OK" in msg:
                out.update({"result": "ssh_ok", "ssh_after_s": round(time.time() - t0, 1), "peek": msg})
                return out
            out["last"] = msg[-200:]
            await asyncio.sleep(10)
        out["result"] = "ssh_recusado"
        return out
    finally:
        await v.destroy(iid)


async def main():
    s = Settings(drive_remote="gdrive:qwen-cache", rclone_config_b64="eA==", embed_mode="gpu", image=IMAGE)
    real_env, real_onstart = render_env(s), load_onstart()
    sleeper = "#!/bin/bash" + NL + "sleep 3600" + NL
    v = RealVast(os.environ["VAST_API_KEY"], "https://console.vast.ai/api", "RTX 3090", 1)
    q = {"verified": {"in": [True, False]}, "external": {"in": [True, False]}, "rentable": {"eq": True}, "type": "on-demand", "limit": 60, "order": [["dph_total", "asc"]],
         "num_gpus": {"eq": 1}, "gpu_name": {"in": ["RTX 3090"]}, "cuda_max_good": {"gte": 13.0}, "inet_down": {"gte": 500}, "reliability2": {"gte": 0.95}, "disk_space": {"gte": 70}}
    offers = [o for o in v._body(await v._req("POST", "/v0/bundles/", json=q)).get("offers", []) if not str(o.get("geolocation") or "").endswith(", CN")]  # noqa: SLF001
    hosts, seen = [], set()
    for o in offers:
        if o["machine_id"] not in seen:
            seen.add(o["machine_id"])
            hosts.append(o)
    credit0 = await v.credit()
    jobs = [one(v, hosts[0], "A_env_e_onstart", real_onstart, real_env), one(v, hosts[1], "B_so_env", sleeper, real_env), one(v, hosts[2], "C_so_onstart", real_onstart, {})]
    res = await asyncio.gather(*jobs, return_exceptions=True)
    out = {"credit_start": credit0, "credit_end": await v.credit(), "results": [r if isinstance(r, dict) else {"erro": repr(r)} for r in res]}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
