"""Runtime do vLLM na GPU fixa: o start_vllm.sh versionado em remote/ e a forma de instala-lo na maquina.

Instalado por SSH logo antes de cada partida do vLLM (como o ditado em stt.py), e NAO pelo bootstrap/onstart da instancia: mexer no onstart muda a
impressao (spec_id) e o controlador destruiria a instancia sob demanda. O comando e idempotente: se o arquivo da maquina ja e igual (sha256), nao faz nada;
se difere, guarda a copia anterior em /workspace/start_vllm.sh.bak_<data> e grava a nova."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

REMOTE = Path(__file__).resolve().parents[2] / "remote"
SCRIPT = REMOTE / "start_vllm.sh"
DESTINO = "/workspace/start_vllm.sh"


def sha256() -> str:
    return hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


def install_command() -> str:
    """Comando unico para `ssh host <cmd>`: o script viaja em base64 dentro do proprio comando."""
    data = SCRIPT.read_bytes()
    b64 = base64.b64encode(data).decode()
    h = hashlib.sha256(data).hexdigest()
    return (
        "mkdir -p /workspace && "
        f"[ \"$(sha256sum {DESTINO} 2>/dev/null | cut -d' ' -f1)\" = {h} ] || "
        f"{{ [ -f {DESTINO} ] && cp {DESTINO} {DESTINO}.bak_$(date +%Y%m%d%H%M%S); "
        f"echo {b64} | base64 -d > {DESTINO} && chmod +x {DESTINO}; }}"
    )
