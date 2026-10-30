"""M1 do estudo de cold start (Spec 14 §5b): o Drive e mais rapido que o Hugging Face para baixar os pesos numa maquina da Vast?

Uma 3090 barata, a imagem enxuta, e dentro dela, em sequencia: (a) 2 GB do model.safetensors pelo Hugging Face (curl com Range),
(b) os mesmos 2 GB pelo Drive (`rclone cat --count`), (c) 1 arquivo menor inteiro pelo Drive com varias conexoes. Mede MB/s de cada.
Destroi no fim. Custo esperado: ~US$ 0,03.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "queue"))
from app.gpu.vast import RealVast  # noqa: E402

NL = chr(10)
VAULT = Path(os.environ.get("VAULT_DIR", r"C:\Users\lucas.rangel\Desktop\claude\personal-skills\secrets"))


def load(name):
    d = {}
    for ln in (VAULT / name).read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            d[k] = v.split(" #")[0].strip()
    return d


def ssh(host, port, key, cmd, timeout=600):
    r = subprocess.run(["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        "-o", "ConnectTimeout=15", "-p", str(port), f"root@{host}", cmd], capture_output=True, timeout=timeout)
    return (r.stdout + r.stderr).decode(errors="replace")


async def main():
    image, out_path, key = sys.argv[1], sys.argv[2], os.environ["SSH_KEY"]
    hf = json.loads((VAULT / "huggingface.json").read_text(encoding="utf-8"))["HF_TOKEN"]
    gpu = load("qwen-gpu.env")
    v = RealVast(load("qwen-api.env")["VAST_API_KEY"], "https://console.vast.ai/api", "RTX 3090", 1)
    credit0 = await v.credit()
    q = {"verified": {"in": [True, False]}, "external": {"in": [True, False]}, "rentable": {"eq": True}, "type": "on-demand", "limit": 60, "order": [["dph_total", "asc"]],
         "num_gpus": {"eq": 1}, "gpu_name": {"in": ["RTX 3090"]}, "cuda_max_good": {"gte": 13.0}, "inet_down": {"gte": 800}, "reliability2": {"gte": 0.95}, "disk_space": {"gte": 50}}
    offers = [o for o in v._body(await v._req("POST", "/v0/bundles/", json=q)).get("offers", []) if not str(o.get("geolocation") or "").endswith(", CN")]  # noqa: SLF001
    rec = {"credit_start": credit0, "image": image}
    iid = None
    try:
        for o in offers[:6]:
            t0 = time.time()
            res = await v.create_instance(int(o["id"]), image, 50, f"m1-{int(t0)}", "#!/bin/bash" + NL + "sleep 3600" + NL, {})
            if not res.ok:
                continue
            iid = res.instance_id
            inst = None
            while time.time() - t0 < 840:
                inst = await v.get_instance(iid)
                if inst and inst.running:
                    break
                await asyncio.sleep(5)
            if inst and inst.running:
                rec.update({"host": o["machine_id"], "geo": o.get("geolocation"), "inet_down_mbps": o.get("inet_down"), "running_s": round(time.time() - t0, 1)})
                break
            await v.destroy(iid)
            iid = None
        if not iid:
            rec["result"] = "sem_instancia"
            return
        h, p = inst.ssh_host, int(inst.ssh_port)
        for _ in range(15):
            if ssh(h, p, key, "echo ok").strip().endswith("ok"):
                break
            time.sleep(6)
        conf = base64.b64encode(base64.b64decode(gpu["RCLONE_CONFIG_B64"])).decode()
        setup = ("mkdir -p /workspace/m1 && cd /workspace/m1 && (command -v rclone >/dev/null || curl -fsSL https://rclone.org/install.sh | bash >/dev/null 2>&1); "
                 f"echo {conf} | base64 -d > rclone.conf; rclone version | head -1")
        rec["setup"] = ssh(h, p, key, setup, 300).strip()[-120:]
        t = time.time()
        a = ssh(h, p, key, f"cd /workspace/m1; curl -sL -r 0-1999999999 -H 'Authorization: Bearer {hf}' https://huggingface.co/twolven/Qwen3.8-27B-abliterated-AWQ-MTP/resolve/main/model.safetensors -o /dev/null -w '%{{size_download}} bytes %{{time_total}}s %{{speed_download}} B/s'", 900)
        rec["hf_range_2GB"] = a.strip()[-200:]
        b = ssh(h, p, key, "cd /workspace/m1; S=$(date +%s.%N); rclone --config rclone.conf cat --count 2000000000 gdrive:qwen-cache/llm/model.safetensors | wc -c; E=$(date +%s.%N); echo \"$S $E\" | awk '{printf \"drive_cat %.1fs\\n\", $2-$1}'", 900)
        rec["drive_cat_2GB"] = b.strip()[-200:]
        c = ssh(h, p, key, "cd /workspace/m1; ls -la 2>/dev/null | head -2 >/dev/null; S=$(date +%s.%N); rclone --config rclone.conf copy gdrive:qwen-cache/llm/model-mtp.safetensors . --transfers 8 --multi-thread-streams 8 --drive-chunk-size 128M -v 2>&1 | tail -1; E=$(date +%s.%N); ls -l model-mtp.safetensors | awk '{print $5}'; echo \"$S $E\" | awk '{printf \"drive_copy_mtp %.1fs\\n\", $2-$1}'", 900)
        rec["drive_copy_mtp"] = c.strip()[-260:]
        rec["result"] = "ok"
    finally:
        if iid:
            await v.destroy(iid)
        rec["credit_end"] = await v.credit()
        rec["spent_usd"] = round(credit0 - rec["credit_end"], 3)
        Path(out_path).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
