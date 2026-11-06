"""Ditado: servico de transcricao (faster-whisper, no PROCESSADOR da maquina do LLM, porta 18002).

Instalado por SSH logo depois que o tunel abre (como a maquina de midia), e NAO pelo bootstrap da instancia: mexer no onstart muda a
impressao (spec_id) e o controlador destruiria a instancia. O comando e idempotente: se o servico ja responde, nao faz nada."""
from __future__ import annotations

import base64
from pathlib import Path

REMOTE = Path(__file__).resolve().parents[2] / "remote"

# roda no host da GPU; baixa prioridade para nunca atrapalhar o vLLM; a porta so escuta em 127.0.0.1
PROVISION = r"""#!/bin/bash
curl -s -m 3 http://127.0.0.1:18002/health >/dev/null && exit 0
mkdir -p /workspace/stt && cd /workspace/stt
echo "$STT_SERVER_B64" | base64 -d > stt_server.py
if [ ! -x venv/bin/uvicorn ]; then
  python3 -m venv venv || exit 1
  nice -n 15 ./venv/bin/pip install -q --no-cache-dir faster-whisper fastapi uvicorn python-multipart "av>=14,<17" || exit 1
fi
for p in $(pgrep -f "[u]vicorn stt_server"); do kill "$p"; done
setsid nohup nice -n 15 env STT_THREADS=${STT_THREADS:-16} HF_HOME=/workspace/stt/hf ./venv/bin/uvicorn stt_server:app --host 127.0.0.1 --port 18002 > server.log 2>&1 < /dev/null &
for _ in $(seq 1 60); do curl -s -m 2 http://127.0.0.1:18002/health >/dev/null && exit 0; sleep 3; done
exit 1
"""


def command(threads: int = 16) -> str:
    """Comando unico para `ssh host <cmd>`: o servidor Python viaja em base64 dentro do proprio comando."""
    srv = base64.b64encode((REMOTE / "stt_server.py").read_bytes()).decode()
    script = base64.b64encode(PROVISION.encode()).decode()
    return f"STT_SERVER_B64='{srv}' STT_THREADS={int(threads)} bash -c \"$(echo {script} | base64 -d)\""
