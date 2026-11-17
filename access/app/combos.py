"""Combos de IA do ecossistema (spec 40 §3): apelidos de modelo com uma lista ordenada de destinos.

- `rt-planejar`: planejamento (Claude Code quando a conta estiver ativa; hoje ChatGPT), com a IA da casa de reserva.
- `rt-geral`: o dia a dia dos produtos (MiniMax quando as contas entrarem; hoje a IA da casa).
- `rt-rapido`, `rt-livre` e o roteador `rt-auto` (spec 45): ver app/roteador.py.
Destino `{"router": "<modelo do 9Router global>"}` ou `{"casa": "<modelo do llm-queue>"}`. O primeiro que responder vence; erro ou
resposta vazia passa para o próximo. A lista fica em `settings.combos` e muda pelo painel (POST /admin/api/combos), sem deploy.
"""
from __future__ import annotations

import json

from fastapi import Request

from app.admin import _json, api, require
from app.ctx import fail

PADRAO = {
    # spec 45: os destinos "router" de um combo giram em rodízio (Claude <-> GPT); a casa fica sempre por último, de reserva
    "rt-planejar": [{"router": "cc/claude-opus-5-5"}, {"router": "cx/gpt-6-sol"}, {"casa": "qwen-abliterated"}],
    "rt-rapido": [{"router": "cc/claude-haiku-4-5-20251001"}, {"router": "cx/gpt-6-luna"}, {"casa": "qwen-abliterated"}],
    "rt-livre": [{"casa": "qwen-abliterated"}],
    "rt-geral": [{"casa": "qwen-abliterated"}],
}


def combos(store) -> dict:
    raw = store.get_setting("combos")
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        v = {}
    out = {k: list(x) for k, x in PADRAO.items()}
    for k, lista in (v or {}).items():
        if isinstance(lista, list) and lista:
            out[k] = lista
    return out


def validar(novo) -> dict:
    if not isinstance(novo, dict) or not novo:
        raise fail(422, "envie {apelido: [destinos]}", "invalid_request_error", "combos_invalidos")
    limpo = {}
    for apelido, lista in novo.items():
        if not str(apelido).startswith("rt-") or not isinstance(lista, list) or not (1 <= len(lista) <= 6):
            raise fail(422, f"combo inválido: {apelido}", "invalid_request_error", "combos_invalidos")
        dest = []
        for d in lista:
            if isinstance(d, dict) and len(d) == 1 and next(iter(d)) in ("router", "casa") and str(next(iter(d.values()))).strip():
                dest.append({next(iter(d)): str(next(iter(d.values()))).strip()[:120]})
            else:
                raise fail(422, f"destino inválido em {apelido}", "invalid_request_error", "combos_invalidos")
        limpo[str(apelido)] = dest
    return limpo


@api.get("/combos")
async def combos_get(request: Request):
    ctx = require(request)
    return {"combos": combos(ctx.store), "padrao": PADRAO}


@api.post("/combos")
async def combos_set(request: Request):
    ctx = require(request)
    b = await _json(request)
    limpo = validar(b.get("combos"))
    atual = combos(ctx.store)
    atual.update(limpo)
    ctx.store.set_setting("combos", json.dumps(atual, ensure_ascii=False))
    return {"combos": atual}
