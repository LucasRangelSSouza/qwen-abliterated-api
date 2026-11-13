"""Painel: Fornecedores e precos (/admin/api/fornecedores). Chaves so de escrita (mostra os 4 ultimos), saldos ao vivo, precificacao do Estudio."""
from __future__ import annotations

import asyncio

from fastapi import Request

from app import estudio, fornecedores as F, imagem
from app.admin import _db, _json, api, require
from app.ctx import fail


async def _visao(ctx) -> dict:
    k, origem = F.chave(ctx, "runware")
    rw, vast = await asyncio.gather(F.saldo_runware(ctx, k), F.saldo_vast(ctx))
    p = F.precos(ctx.store)
    tabela = []
    for nome, e in estudio.CATALOGO.items():
        q = e["padrao"]
        exemplo = {"segundo": 10, "caractere": 1000, "imagem": 1}[e["unidade"]]
        custo = e["taxas"][q] * exemplo
        pf = F.preco_final(ctx.store, nome, custo)
        tabela.append({"efeito": nome, "rotulo": e["rotulo"], "kind": e["kind"], "modelo": e["modelo"], "unidade": e["unidade"], "qualidade": q,
                       "taxas_usd": e["taxas"], "exemplo": {"quantidade": exemplo, "custo_usd": round(custo, 4), **pf},
                       "markup": p["por_efeito"].get(nome)})
    return {
        "secrets_key_set": bool(ctx.s.secrets_key), "mock": ctx.s.runware_mock,
        "fornecedores": [
            {"id": "runware", "nome": "Runware", "uso": F.PROVEDORES["runware"]["uso"], "site": F.PROVEDORES["runware"]["site"],
             "chave": F.mascara(k), "origem": origem, "saldo": rw},
            {"id": "vast", "nome": "Vast.ai", "uso": "GPU do LLM (2×3090)", "site": "https://cloud.vast.ai/billing/", "chave": "no llm-queue", "origem": "ambiente", "saldo": vast},
            {"id": "chatgpt", "nome": "ChatGPT (9Router)", "uso": "Geração de imagem pelo combo do 9Router", "site": None, "chave": ("modelo " + ctx.s.image_router_model) if imagem.ligado(ctx.s) else None, "origem": "9router",
             "saldo": {"ok": True, "assinatura": True} if imagem.ligado(ctx.s) else {"ok": False, "erro": "IMAGE_ROUTER_KEY ausente: imagem vai para a GPU"}},
        ],
        "precos": p, "tabela": tabela, "tabela_de": estudio.TABELA_LIDA_EM,
    }


@api.get("/fornecedores")
async def fornecedores_get(request: Request):
    return await _visao(require(request))


@api.post("/fornecedores/chave")
async def fornecedores_chave(request: Request):
    ctx = require(request)
    b = await _json(request)
    prov, k = str(b.get("fornecedor") or ""), b.get("chave")
    if k:
        teste = await F.saldo_runware(ctx, str(k).strip()) if prov == "runware" else {"ok": True}
        if not teste.get("ok"):
            raise fail(422, f"A chave não funcionou: {teste.get('erro')}", "invalid_request_error", "chave_invalida")
    try:
        F.salvar_chave(ctx, prov, k)
    except F.FornecedorErro as e:
        raise fail(422, str(e), "invalid_request_error", "fornecedor_erro")
    return await _visao(ctx)


@api.post("/fornecedores/precos")
@_db
async def fornecedores_precos(request: Request):
    ctx = require(request)
    try:
        F.salvar_precos(ctx.store, await _json(request))
    except F.FornecedorErro as e:
        raise fail(422, str(e), "invalid_request_error", "preco_invalido")
    return await _visao(ctx)
