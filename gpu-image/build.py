"""Gera o Dockerfile da imagem enxuta da GPU (Spec 14 §5c, variante V2).

A imagem base (vastai/vllm) tem 23,7 GB descompactados e uma camada de 5,1 GB, que o Docker descompacta em um so nucleo.
Medido em 30/10: 6,2 GB sao binarios de kernel para GPUs Blackwell de datacenter (sm100/sm103/sm107/sm110: trtllm-gen do
flashinfer), que a RTX 3090 (sm_86) nunca carrega. A imagem nova: (1) tira esses binarios; (2) refaz as camadas separando os
diretorios grandes, para o Docker baixar e descompactar varias em paralelo; (3) repete a configuracao da base (ENV, ENTRYPOINT...).

Uso: python build.py <imagem-base> > Dockerfile
"""
import json
import subprocess
import sys

BASE = sys.argv[1]
DP = "usr/local/lib/python3.12/dist-packages"
# diretorios grandes que ganham camada propria (caminho sem a barra inicial, como o --exclude espera)
BIG = [f"{DP}/nvidia", f"{DP}/flashinfer_jit_cache", "usr/local/cuda-13.0", f"{DP}/torch", f"{DP}/triton", f"{DP}/vllm"]
# o que sai da imagem: kernels trtllm-gen para Blackwell de datacenter (inuteis em Ampere)
DROP = [f"{DP}/flashinfer_cubin/cubins"]


def q(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


cfg = json.loads(subprocess.check_output(["docker", "image", "inspect", BASE]))[0]["Config"]
out = ["# syntax=docker/dockerfile:1.7-labs", f"FROM {BASE} AS slim"]
out.append("RUN " + " && ".join(f"rm -rf /{p} && mkdir -p /{p}" for p in DROP))
out.append("FROM scratch")
for i, p in enumerate(BIG):
    out.append(f"COPY --from=slim /{p} /{p}")
ex = " ".join(f"--exclude={p}" for p in BIG)
out.append(f"COPY --from=slim {ex} /usr/local /usr/local")
out.append("COPY --from=slim --exclude=usr/local /usr /usr")
out.append("COPY --from=slim --exclude=usr / /")
for e in cfg.get("Env") or []:
    k, _, v = e.partition("=")
    out.append(f"ENV {k}={q(v)}")
if cfg.get("WorkingDir"):
    out.append(f"WORKDIR {cfg['WorkingDir']}")
if cfg.get("User"):
    out.append(f"USER {cfg['User']}")
for port in (cfg.get("ExposedPorts") or {}):
    out.append(f"EXPOSE {port}")
if cfg.get("Entrypoint"):
    out.append("ENTRYPOINT " + json.dumps(cfg["Entrypoint"]))
if cfg.get("Cmd"):
    out.append("CMD " + json.dumps(cfg["Cmd"]))
out.append('LABEL org.opencontainers.image.source="https://github.com/LucasRangelSSouza/qwen-abliterated-api"')
out.append(f'LABEL qwen.base="{BASE}" qwen.slim="flashinfer_cubin sm100+ removido; camadas separadas"')
print("\n".join(out))
