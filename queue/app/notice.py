"""Aviso de espera dentro do stream (Spec 14 §3): quem pede com a GPU desligada ve, na hora, que o recurso esta sendo ligado
e que a demora nao e erro. Texto fixo, em portugues, sem chamar o modelo."""
from __future__ import annotations

import json
import time
import uuid

MODEL = "qwen-abliterated"

PHASES = {
    "STARTING": ("starting", "preparando a partida"),
    "FALLBACK": ("renting", "alugando e preparando a máquina"),
    "LOADING": ("loading_model", "carregando o modelo"),
    "OFF": ("starting", "preparando a partida"),
}


def _minutes(eta_s: float | None) -> str:
    m = max(1, round((eta_s or 60) / 60))
    return "1 minuto" if m == 1 else f"{m} minutos"


def first_text(path: str) -> str:
    if path == "restart":
        quando = "de 2 a 4 minutos"
    elif path == "known":
        quando = "de 5 a 8 minutos"
    else:
        quando = "de 8 a 18 minutos (é preciso alugar uma máquina nova)"
    return (f"O computador da IA estava desligado e está sendo ligado agora. Isso leva {quando} e não é erro: "
            "sua pergunta já está na fila e será respondida automaticamente.\n")


def update_text(state: str, eta_s: float | None) -> str:
    _, etapa = PHASES.get(state, PHASES["STARTING"])
    return f"Ainda ligando o computador da IA ({etapa}). Faltam cerca de {_minutes(eta_s)}. Sua pergunta continua na fila.\n"


def ready_text() -> str:
    return "Computador da IA pronto. Respondendo agora.\n\n"


def x_queue(state: str, eta_s: float | None, elapsed_s: float, message: str, path: str) -> dict:
    phase = "ready" if state == "READY" else PHASES.get(state, PHASES["STARTING"])[0]
    return {"state": state, "phase": phase, "path": path, "eta_s": None if eta_s is None else int(eta_s),
            "elapsed_s": int(elapsed_s), "message": message.strip()}


def chunk(text: str, xq: dict, fields: tuple[str, ...], completion_id: str) -> bytes:
    """Chunk no formato chat.completion.chunk da OpenAI. `fields` diz em qual campo do delta vai o texto
    (reasoning e reasoning_content por padrao; content so quando o cliente pede com X-Queue-Notice: content)."""
    delta = {"role": "assistant"}
    for f in fields:
        delta[f] = text
    payload = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
               "choices": [{"index": 0, "delta": delta, "finish_reason": None}], "x_queue": xq}
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"


def new_id() -> str:
    return "chatcmpl-queue-" + uuid.uuid4().hex[:12]
