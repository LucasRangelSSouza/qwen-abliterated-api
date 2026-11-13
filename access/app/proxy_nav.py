"""Proxy do navegador por cliente (spec 31 §2.1): as VMs saem por IP de datacenter (Contabo, Alemanha) e o Instagram e afins barram.
O painel cadastra um proxy (de preferencia residencial/ISP do Brasil, IP fixo) por pessoa; o app busca na abertura e passa por ele SO os
dominios que barram datacenter. A URL (com usuario e senha) fica cifrada no banco (Fernet com SECRETS_KEY) e nunca volta inteira ao painel.
Sem cadastro para a pessoa, vale o padrao (settings proxy_nav_padrao), e sem padrao o app sai direto."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request

from app.admin import _db, api, require
from app.ctx import Ctx, fail

router = APIRouter()

DOMINIOS_PADRAO = ["instagram.com", "cdninstagram.com", "facebook.com", "fbcdn.net", "messenger.com", "threads.net", "tiktok.com",
                   "tiktokcdn.com", "tiktokv.com", "byteoversea.com", "whatsapp.com", "whatsapp.net", "x.com", "twitter.com", "twimg.com",
                   "linkedin.com", "licdn.com"]
ESQUEMAS = ("http", "https", "socks5")


def _fernet(ctx: Ctx) -> Fernet:
    seg = ctx.s.secrets_key
    if not seg:
        raise fail(503, "SECRETS_KEY nao configurada: o proxy nao pode ser guardado", "service_unavailable", "no_secrets_key")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("proxy-nav:" + seg).encode()).digest()))


def _tabela(ctx: Ctx) -> dict:
    try:
        v = json.loads(ctx.store.get_setting("proxy_nav") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def validar_url(url: str) -> str:
    u = str(url or "").strip()
    p = urlsplit(u)
    if p.scheme not in ESQUEMAS or not p.hostname or not p.port:
        raise ValueError("use esquema://usuario:senha@host:porta (http, https ou socks5)")
    return u


def mascarar(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{'***@' if p.username else ''}{p.hostname}:{p.port}"


def dominios(ctx: Ctx) -> list[str]:
    try:
        v = json.loads(ctx.store.get_setting("proxy_nav_dominios") or "null")
    except ValueError:
        v = None
    return v if isinstance(v, list) and v else DOMINIOS_PADRAO


def guardar(ctx: Ctx, chave: str, url: str | None) -> None:
    """chave: id da pessoa (texto) ou "padrao"; url None apaga."""
    t = _tabela(ctx)
    if url is None:
        t.pop(chave, None)
    else:
        t[chave] = _fernet(ctx).encrypt(validar_url(url).encode()).decode()
    ctx.store.set_setting("proxy_nav", json.dumps(t))


def url_de(ctx: Ctx, person_id: int | None) -> str | None:
    t = _tabela(ctx)
    tok = t.get(str(person_id)) if person_id is not None else None
    tok = tok or t.get("padrao")
    if not tok:
        return None
    try:
        return _fernet(ctx).decrypt(tok.encode()).decode()
    except (InvalidToken, Exception):  # noqa: BLE001  chave mudou: trata como sem proxy
        return None


# ---- painel
@api.get("/proxy-nav")
@_db
def painel_listar(request: Request):
    ctx = require(request)
    out = {}
    for k in _tabela(ctx):
        u = url_de(ctx, None) if k == "padrao" else url_de(ctx, int(k)) if k.isdigit() else None
        if u:
            out[k] = mascarar(u)
    return {"proxies": out, "dominios": dominios(ctx), "secrets_key": bool(ctx.s.secrets_key)}


@api.put("/proxy-nav/{chave}")
@_db
async def painel_gravar(chave: str, request: Request):
    ctx = require(request)
    if chave != "padrao" and not chave.isdigit():
        raise fail(422, "use o id da pessoa ou 'padrao'", "invalid_request_error", "invalid_key")
    b = await request.json()
    try:
        guardar(ctx, chave, b.get("url") if b.get("url") else None)
    except ValueError as e:
        raise fail(422, str(e), "invalid_request_error", "invalid_proxy")
    return painel_listar(request)


@api.put("/proxy-nav-dominios")
@_db
async def painel_dominios(request: Request):
    ctx = require(request)
    b = await request.json()
    v = b.get("dominios")
    if v is None:
        ctx.store.set_setting("proxy_nav_dominios", "")
    elif not isinstance(v, list) or not all(isinstance(x, str) and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", x) for x in v):
        raise fail(422, "dominios deve ser uma lista de dominios (ex.: instagram.com)", "invalid_request_error", "invalid_domains")
    else:
        ctx.store.set_setting("proxy_nav_dominios", json.dumps(v))
    return painel_listar(request)


# ---- app: o navegador integrado pede o proxy da conta ao abrir (chave da pessoa); nunca registrado em log
@router.get("/v1/app/proxy-navegador")
@_db
def app_proxy(request: Request):
    from app.proxy import authenticate
    ctx = request.app.state.ctx
    p = authenticate(ctx, request)
    url = url_de(ctx, p.person_id if p.kind == "person" else None)
    return {"proxy": url, "dominios": dominios(ctx) if url else []}
