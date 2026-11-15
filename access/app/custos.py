"""Aba de custos do painel: quanto o ecossistema custa por mês e quanto carregar no cartão pré-pago.

- Fixos: lista editável no painel (`settings.custos_fixos`), cada item com nome, para que serve, valor e moeda (BRL/USD).
- Vast: custo programado por mês das máquinas ligadas (llm-queue `/admin/billing`, `burn_per_month_usd`).
- Runware: gasto dos últimos 30 dias (API da Runware), como estimativa do mês.
- Cotação do dólar do dia (AwesomeAPI, cache de 6 h; reserva 5,60).
- Receita: soma dos planos ativos dos produtos do ecossistema (painel de produtos).
"""
from __future__ import annotations

import json
import time

import httpx
from fastapi import Request

from app import fornecedores
from app.admin import _json, api, require
from app.ctx import fail

FOLGA = 0.15
COTACAO_RESERVA = 5.60
_cotacao = {"v": None, "t": 0.0}

FIXOS_PADRAO = [
    {"nome": "Servidor principal (Contabo)", "para_que": "Sites, painel, fila, banco e e-mails", "valor": 0, "moeda": "BRL"},
    {"nome": "Proxy do navegador (KingHost)", "para_que": "Saída pelo Brasil para redes sociais e YouTube", "valor": 0, "moeda": "BRL"},
    {"nome": "Domínio e e-mail (Hostinger)", "para_que": "rangeltech.net e contato@", "valor": 0, "moeda": "BRL"},
    {"nome": "ChatGPT (OAuth)", "para_que": "Imagens (gpt-image) e visão", "valor": 0, "moeda": "USD"},
    {"nome": "MiniMax (2 contas OAuth)", "para_que": "Divide a carga com a IA da casa", "valor": 0, "moeda": "USD"},
    {"nome": "Claude Code (2 contas OAuth)", "para_que": "Planejamento das tarefas", "valor": 0, "moeda": "USD"},
]


def fixos(store) -> list[dict]:
    raw = store.get_setting("custos_fixos")
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        v = None
    return v if isinstance(v, list) else [dict(x) for x in FIXOS_PADRAO]


def salvar_fixos(store, itens) -> list[dict]:
    if not isinstance(itens, list) or len(itens) > 60:
        raise fail(422, "lista de custos inválida", "invalid_request_error", "custos_invalidos")
    limpos = []
    for it in itens:
        nome = str((it or {}).get("nome") or "").strip()[:80]
        if not nome:
            continue
        try:
            valor = max(0.0, round(float(str(it.get("valor") or 0).replace(",", ".")), 2))
        except ValueError:
            raise fail(422, f"valor inválido em {nome}", "invalid_request_error", "valor_invalido")
        limpos.append({"nome": nome, "para_que": str(it.get("para_que") or "")[:120], "valor": valor, "moeda": "USD" if it.get("moeda") == "USD" else "BRL"})
    store.set_setting("custos_fixos", json.dumps(limpos, ensure_ascii=False))
    return limpos


async def cotacao(ctx) -> float:
    if _cotacao["v"] and time.time() - _cotacao["t"] < 6 * 3600:
        return _cotacao["v"]
    try:
        r = await ctx.http.get("https://economia.awesomeapi.com.br/json/last/USD-BRL", timeout=8)
        v = float(r.json()["USDBRL"]["bid"])
        _cotacao.update(v=v, t=time.time())
        return v
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return _cotacao["v"] or COTACAO_RESERVA


async def _vast_mes(ctx) -> dict:
    try:
        r = await ctx.http.get(ctx.s.upstream_base + "/admin/billing", headers={"x-admin-key": ctx.s.upstream_admin_key}, timeout=15)
        b = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        b = {}
    if "burn_per_month_usd" not in b:
        return {"ok": False}
    return {"ok": True, "mes_usd": float(b["burn_per_month_usd"]), "saldo_usd": float(b.get("credit_usd") or 0), "dias": b.get("days_of_balance")}


@api.get("/custos")
async def custos(request: Request):
    ctx = require(request)
    usd = await cotacao(ctx)
    itens = []
    for f in fixos(ctx.store):
        brl = f["valor"] * (usd if f["moeda"] == "USD" else 1)
        itens.append({**f, "tipo": "fixo", "brl_mes": round(brl, 2)})
    v = await _vast_mes(ctx)
    itens.append({"nome": "GPU da IA (Vast)", "para_que": "A IA da casa e as máquinas de mídia ligadas agora", "tipo": "medido",
                  "valor": round(v.get("mes_usd", 0), 2), "moeda": "USD", "brl_mes": round(v.get("mes_usd", 0) * usd, 2), "ok": v["ok"],
                  "detalhe": (f"saldo US$ {v['saldo_usd']:.2f}, dá para {v['dias']} dias" if v["ok"] and v.get("dias") is not None else "sem leitura agora")})
    try:
        rw = await fornecedores.saldo_runware(ctx)
    except Exception:  # noqa: BLE001
        rw = {"ok": False}
    g = float(rw.get("gasto_30d_usd") or 0) if rw.get("ok") else 0.0
    itens.append({"nome": "Vídeo e voz de ponta (Runware)", "para_que": "Estúdio: gasto dos últimos 30 dias", "tipo": "medido",
                  "valor": round(g, 2), "moeda": "USD", "brl_mes": round(g * usd, 2), "ok": bool(rw.get("ok")),
                  "detalhe": (f"saldo US$ {float(rw['saldo_usd']):.2f}" if rw.get("ok") else "sem chave ou sem leitura")})
    total = round(sum(i["brl_mes"] for i in itens), 2)
    receita = 0.0
    try:
        from app import produtos as prod
        for p in prod._lista():
            res = await prod._get(ctx, p, "/api/admin/resumo")
            receita += float(res.get("receita_mensal") or 0)
    except Exception:  # noqa: BLE001
        pass
    return {"cotacao_usd": round(usd, 4), "itens": itens, "total_brl_mes": total, "folga": FOLGA,
            "carregar_brl": round(total * (1 + FOLGA), 2), "receita_produtos_brl": round(receita, 2)}


@api.post("/custos/fixos")
async def custos_fixos(request: Request):
    ctx = require(request)
    b = await _json(request)
    return {"fixos": salvar_fixos(ctx.store, b.get("fixos"))}
