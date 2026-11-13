"""Fornecedores (spec 36): chaves de API, saldos e precificacao do Estudio, tudo administrado pelo painel.

- Chaves: cifradas (Fernet com SECRETS_KEY) em `settings.provider_keys`; o painel grava e testa, nunca le de volta (so os 4 ultimos caracteres).
  Ordem de leitura: banco (painel) -> variavel de ambiente (RUNWARE_API_KEY). Sem SECRETS_KEY o painel avisa e a variavel continua valendo.
- Saldos: Runware (`accountManagement/getDetails`, gratis), Vast (credito lido do llm-queue `/admin/billing`).
- Precificacao do Estudio (`settings.estudio_precos`): preco = custo x markup (padrao 5), com piso de custo x markup_minimo (1,5) e teto
  de R$ 20 por pedido (cap_brl): passa do teto so quando o piso exige, e a tela avisa antes ("acima_do_teto").
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app import credits

log = logging.getLogger("llm-access.fornecedores")

PROVEDORES = {
    "runware": {"nome": "Runware", "env": "runware_api_key", "uso": "Estúdio: vídeo, voz, sincronia labial e imagem de ponta", "site": "https://runware.ai/dashboard"},
}
PRECOS_PADRAO = {"markup": 5.0, "markup_minimo": 1.5, "cap_brl": 20.0, "por_efeito": {}}


class FornecedorErro(Exception):
    pass


def _fernet(ctx) -> Fernet:
    seg = ctx.s.secrets_key
    if not seg:
        raise FornecedorErro("SECRETS_KEY não configurada no servidor: a chave não pode ser guardada no painel (a variável de ambiente continua valendo).")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fornecedores:" + seg).encode()).digest()))


def _chaves_cifradas(ctx) -> dict:
    raw = ctx.store.get_setting("provider_keys")
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def salvar_chave(ctx, provedor: str, chave: str | None) -> None:
    if provedor not in PROVEDORES:
        raise FornecedorErro("fornecedor desconhecido")
    todas = _chaves_cifradas(ctx)
    chave = (chave or "").strip()
    if chave:
        todas[provedor] = _fernet(ctx).encrypt(chave.encode()).decode()
    else:
        todas.pop(provedor, None)
    ctx.store.set_setting("provider_keys", json.dumps(todas))


def chave(ctx, provedor: str) -> tuple[str, str]:
    """(chave, origem) com origem 'painel' | 'ambiente' | ''. Nunca registra a chave."""
    c = _chaves_cifradas(ctx).get(provedor)
    if c and ctx.s.secrets_key:
        try:
            return _fernet(ctx).decrypt(c.encode()).decode(), "painel"
        except (InvalidToken, ValueError):
            log.warning("chave de %s no painel nao abre (SECRETS_KEY mudou?)", provedor)
    env = getattr(ctx.s, PROVEDORES[provedor]["env"], "") or ""
    return (env, "ambiente") if env else ("", "")


def mascara(k: str) -> str | None:
    return ("••••" + k[-4:]) if k else None


# ---------------------------------------------------------------- saldos

async def saldo_runware(ctx, k: str | None = None) -> dict:
    k = k if k is not None else chave(ctx, "runware")[0]
    if ctx.s.runware_mock and not k:
        return {"ok": True, "saldo_usd": 999.0, "simulado": True}
    if not k:
        return {"ok": False, "erro": "sem chave"}
    try:
        r = await ctx.http.post(ctx.s.runware_url, json=[{"taskType": "accountManagement", "taskUUID": str(uuid.uuid4()), "operation": "getDetails"}],
                                headers={"Authorization": f"Bearer {k}"}, timeout=20)
        d = (r.json().get("data") or [{}])[0] if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "erro": f"sem resposta ({type(e).__name__})"}
    if r.status_code in (401, 403):
        return {"ok": False, "erro": "chave recusada"}
    if "balance" not in d:
        return {"ok": False, "erro": f"resposta inesperada ({r.status_code})"}
    uso = d.get("usage") or {}
    return {"ok": True, "saldo_usd": float(d["balance"]), "organizacao": d.get("organizationName"),
            "gasto_hoje_usd": float((uso.get("today") or {}).get("credits") or 0), "gasto_30d_usd": float((uso.get("last30Days") or {}).get("credits") or 0),
            "pedidos_30d": int((uso.get("last30Days") or {}).get("requests") or 0)}


async def saldo_vast(ctx) -> dict:
    try:
        r = await ctx.http.get(ctx.s.upstream_base + "/admin/billing", headers={"x-admin-key": ctx.s.upstream_admin_key}, timeout=15)
        b = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        b = {}
    if "credit_usd" not in b:
        return {"ok": False, "erro": "llm-queue sem resposta"}
    return {"ok": True, "saldo_usd": float(b["credit_usd"]), "gasto_dia_usd": b.get("burn_per_day_usd"), "dias": b.get("days_of_balance")}


# ---------------------------------------------------------------- precificacao

def precos(store) -> dict:
    raw = store.get_setting("estudio_precos")
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        v = {}
    out = {**PRECOS_PADRAO, **{k: v[k] for k in PRECOS_PADRAO if k in v}}
    out["por_efeito"] = v.get("por_efeito") if isinstance(v.get("por_efeito"), dict) else {}
    return out


def salvar_precos(store, novo: dict) -> dict:
    atual = precos(store)
    for k, lo, hi in (("markup", 1.0, 50.0), ("markup_minimo", 1.0, 10.0), ("cap_brl", 1.0, 5000.0)):
        if k in novo:
            try:
                x = float(novo[k])
            except (TypeError, ValueError):
                raise FornecedorErro(f"{k} precisa ser número")
            if not lo <= x <= hi:
                raise FornecedorErro(f"{k} deve ficar entre {lo:g} e {hi:g}")
            atual[k] = x
    if atual["markup_minimo"] > atual["markup"]:
        raise FornecedorErro("o markup mínimo não pode passar do markup")
    if isinstance(novo.get("por_efeito"), dict):
        pe = {}
        for ef, m in novo["por_efeito"].items():
            if m in (None, ""):
                continue
            try:
                m = float(m)
            except (TypeError, ValueError):
                raise FornecedorErro(f"markup de {ef} precisa ser número")
            if not 1.0 <= m <= 50.0:
                raise FornecedorErro(f"markup de {ef} deve ficar entre 1 e 50")
            pe[str(ef)] = m
        atual["por_efeito"] = pe
    store.set_setting("estudio_precos", json.dumps(atual))
    return atual


def preco_final(store, efeito: str, custo_usd: float) -> dict:
    """Preco ao cliente em dolar e reais. Teto em reais (cap_brl) convertido pela cotacao do painel (usd_brl_rate)."""
    p = precos(store)
    cot = credits.usd_brl_rate(store)
    mk = float(p["por_efeito"].get(efeito, p["markup"]))
    piso = custo_usd * min(p["markup_minimo"], mk)
    cheio = custo_usd * mk
    teto = p["cap_brl"] / cot
    bruto = max(piso, min(cheio, teto), 0.01)
    preco = round(bruto, 4)
    return {"preco_usd": preco, "preco_brl": round(bruto * cot, 2), "markup_aplicado": round(bruto / custo_usd, 2) if custo_usd else None,
            "acima_do_teto": piso > teto + 1e-9, "teto_brl": p["cap_brl"], "cotacao": cot}


def valor_unidade_usd(store, kind: str) -> float:
    """Quanto vale 1 unidade de credito de midia em dolar: preco do pacote / unidades do pacote (Spec 24). Video 18/30 = US$ 0,60."""
    u = credits.units_for_pack(store, kind, None) or credits.DEFAULT_UNITS.get(kind, 1)
    return credits.price_usd(store) / max(1, u)
