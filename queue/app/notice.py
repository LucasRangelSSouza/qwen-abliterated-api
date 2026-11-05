"""Aviso de espera dentro do stream (Spec 14 §3 e Spec 22): quem pede enquanto a resposta ainda nao pode comecar ve uma mensagem curta e
neutra. Regra de produto: o cliente nunca le sobre maquina, GPU, aluguel, reinicio ou problema; so que a resposta esta sendo
processada. Os detalhes de infraestrutura ficam em x_queue (para o painel) e nos logs. Texto fixo, em portugues, sem chamar o modelo."""
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

# Mensagem unica para qualquer falha de infraestrutura mostrada ao cliente (o codigo do erro continua tecnico, para programas).
MENSAGEM_FALHA = "Estamos com muito movimento agora e não conseguimos concluir sua solicitação. Tente novamente em instantes."


def first_text(path: str = "", static: bool = False) -> str:
    return "Sua resposta está sendo processada. Estamos com bastante movimento agora, e ela chega automaticamente em breve.\n"


def update_text(state: str = "", eta_s: float | None = None, static: bool = False) -> str:
    return "Ainda processando a sua resposta. Obrigado por aguardar: ela chega automaticamente.\n"


def ready_text() -> str:
    return "Pronto. Respondendo agora.\n\n"


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
