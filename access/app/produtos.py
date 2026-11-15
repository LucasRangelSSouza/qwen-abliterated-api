"""Painel do ecossistema: agrega os produtos separados (Cortaí, Rendaí...) que rodam fora do llm-access.

Cada produto expõe `/api/admin/resumo` e `/api/admin/assinantes` protegidos por uma chave própria. A lista vem de PRODUTOS_ADMIN
(JSON no env: [{"id","nome","url","chave"}]). O Cérebro Pro continua nas abas de sempre (pessoas/assinantes deste serviço).
Produto fora do ar não derruba o painel: volta com `erro`."""
from __future__ import annotations

import json
import os

import httpx
from fastapi import Request

from app.admin import api, require
from app.ctx import fail


def _lista() -> list[dict]:
    try:
        itens = json.loads(os.environ.get("PRODUTOS_ADMIN") or "[]")
    except ValueError:
        return []
    return [p for p in itens if isinstance(p, dict) and p.get("id") and p.get("url")]


async def _get(ctx, p: dict, caminho: str) -> dict:
    try:
        r = await ctx.http.get(p["url"].rstrip("/") + caminho, headers={"x-admin-key": p.get("chave") or ""}, timeout=15)
        if r.status_code != 200:
            return {"erro": f"o produto respondeu {r.status_code}"}
        return r.json()
    except httpx.HTTPError:
        return {"erro": "produto fora do ar ou sem resposta"}


@api.get("/produtos")
async def produtos(request: Request):
    ctx = require(request)
    out = []
    for p in _lista():
        res = await _get(ctx, p, "/api/admin/resumo")
        out.append({"id": p["id"], "nome": p.get("nome") or p["id"], "url": p["url"], **res})
    return {"produtos": out}


@api.get("/produtos/{pid}/assinantes")
async def assinantes(pid: str, request: Request):
    ctx = require(request)
    p = next((x for x in _lista() if x["id"] == pid), None)
    if not p:
        raise fail(404, "produto não cadastrado", "invalid_request_error", "produto_desconhecido")
    return {"id": pid, "nome": p.get("nome") or pid, **(await _get(ctx, p, "/api/admin/assinantes"))}
