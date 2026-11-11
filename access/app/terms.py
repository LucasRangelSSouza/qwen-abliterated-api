"""Termos de uso com aceite registrado e verificavel (Spec 25 §18).

- `terms_versions`: cada versao do texto (arquivo `app/termos/<versao>.md`), com o SHA-256 do texto. Uma versao publicada nunca muda.
- `terms_acceptances`: so inclusao (trigger no banco + o codigo nunca faz UPDATE/DELETE). Cada linha traz o hash da anterior (corrente) e uma
  assinatura HMAC feita com a chave `TERMS_SIGNING_KEY` (ou, se faltar, uma chave gerada uma unica vez e guardada em settings, nunca trocada).
- O comprovante vai por e-mail com o codigo de verificacao (12 primeiros caracteres do hash); `GET /v1/termos/verificar` confere."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Request

from app import mailer
from app.admin import _db, _ip, _json, api, require
from app.ctx import fail
from app.ledger import iso

router = APIRouter()

PASTA = Path(__file__).resolve().parent / "termos"
CODIGO_LEN = 12


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def garantir_versoes(ctx) -> None:
    """Carrega os arquivos de termos no banco (so os que faltam) e marca a maior versao como vigente."""
    arquivos = sorted(PASTA.glob("*.md"))
    if not arquivos:
        return
    existentes = {r["versao"] for r in ctx.store.query("SELECT versao FROM terms_versions")}
    agora = ctx.clock()
    with ctx.store.tx() as c:
        for f in arquivos:
            if f.stem in existentes:
                continue
            texto = f.read_text(encoding="utf-8")
            c.execute("INSERT INTO terms_versions(versao,texto,sha256,publicado_em,vigente) VALUES(?,?,?,?,0)", (f.stem, texto, _sha(texto), agora))
        c.execute("UPDATE terms_versions SET vigente=0")
        c.execute("UPDATE terms_versions SET vigente=1 WHERE versao=?", (arquivos[-1].stem,))


def vigente(ctx):
    """Linha da versao vigente (carrega os arquivos na primeira vez em que o processo precisa)."""
    r = ctx.store.one("SELECT * FROM terms_versions WHERE vigente=1 ORDER BY versao DESC LIMIT 1")
    if r is None or not getattr(ctx, "_termos_carregados", False):
        garantir_versoes(ctx)
        ctx._termos_carregados = True  # noqa: SLF001
        r = ctx.store.one("SELECT * FROM terms_versions WHERE vigente=1 ORDER BY versao DESC LIMIT 1")
    return r


def _chave(ctx) -> bytes:
    k = os.environ.get("TERMS_SIGNING_KEY", "").strip()
    if k:
        return k.encode()
    k = ctx.store.get_setting("terms_signing_key")
    if not k:
        k = secrets.token_hex(32)  # criada uma vez; fica no banco e no cofre (nunca e trocada)
        ctx.store.set_setting("terms_signing_key", k)
    return k.encode()


def _hash_registro(email: str, versao: str, texto_sha: str, aceito_iso: str, ip: str, canal: str, anterior: str) -> str:
    return _sha("|".join([email, versao, texto_sha, aceito_iso, ip, canal, anterior]))


def _assinar(ctx, h: str, chave: bytes | None = None) -> str:
    return hmac.new(chave or _chave(ctx), h.encode(), hashlib.sha256).hexdigest()


def registrar(ctx, email: str, canal: str, ip: str = "", user_agent: str = "", account_id: int | None = None, versao: str | None = None, enviar_comprovante: bool = True) -> dict:
    """Grava o aceite da versao (a vigente, por padrao). Devolve {versao, aceito_em, codigo, hash}. Aceitar de novo a mesma versao nao duplica."""
    v = vigente(ctx)
    if v is None:
        raise fail(503, "termos indisponiveis", "service_unavailable", "terms_unavailable")
    if versao and versao != v["versao"]:
        raise fail(409, "a versao dos termos mudou; recarregue a pagina e leia de novo", "invalid_request_error", "terms_outdated", versao=v["versao"])
    email = (email or "").strip().lower()
    ja = ctx.store.one("SELECT * FROM terms_acceptances WHERE email=? AND versao=? ORDER BY id LIMIT 1", (email, v["versao"]))
    if ja is not None:
        if account_id and ja["account_id"] is None:
            pass  # o aceite do checkout vale; a conta ainda nao existia
        return {"versao": ja["versao"], "aceito_em": ja["aceito_iso"], "codigo": ja["hash"][:CODIGO_LEN], "hash": ja["hash"], "novo": False}
    agora = ctx.clock()
    aceito_iso = iso(agora).replace("Z", f".{int((agora % 1) * 1000):03d}Z")
    chave = _chave(ctx)  # antes da transacao (a primeira chamada grava a chave em settings)
    with ctx.store.tx() as c:  # a transacao serializa (lock consultivo no Postgres, BEGIN IMMEDIATE no SQLite): a corrente nao se bifurca
        ult = c.execute("SELECT hash FROM terms_acceptances ORDER BY id DESC LIMIT 1").fetchone()
        anterior = ult["hash"] if ult else ""
        h = _hash_registro(email, v["versao"], v["sha256"], aceito_iso, ip, canal, anterior)
        c.execute("INSERT INTO terms_acceptances(account_id,email,versao,texto_sha256,aceito_em,aceito_iso,ip,user_agent,canal,hash_anterior,hash,assinatura) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (account_id, email, v["versao"], v["sha256"], agora, aceito_iso, ip[:60], (user_agent or "")[:300], canal[:20], anterior, h, _assinar(ctx, h, chave)))
    out = {"versao": v["versao"], "aceito_em": aceito_iso, "codigo": h[:CODIGO_LEN], "hash": h, "novo": True}
    if enviar_comprovante:
        try:
            mailer.send_terms_receipt(ctx, email, v["versao"], aceito_iso, h[:CODIGO_LEN])
        except Exception:  # noqa: BLE001  e-mail nunca derruba o aceite
            pass
    return out


def aceitou(ctx, email: str, account_id: int | None = None) -> str | None:
    """Versao mais nova que o e-mail (ou a conta) aceitou, ou None."""
    r = ctx.store.one("SELECT versao FROM terms_acceptances WHERE email=? OR (account_id IS NOT NULL AND account_id=?) ORDER BY id DESC LIMIT 1", ((email or "").lower(), account_id or -1))
    return r["versao"] if r else None


def exigir_aceite(ctx, b: dict, request: Request, email: str, canal: str, account_id: int | None = None) -> dict:
    """Usado nas rotas que criam conta: sem a caixa marcada devolve 400; com ela registra o aceite."""
    if not b.get("aceite_termos"):
        raise fail(400, "Para continuar, leia e aceite os Termos de uso.", "invalid_request_error", "terms_required")
    return registrar(ctx, email, canal, ip=_ip(request), user_agent=request.headers.get("user-agent", ""), account_id=account_id, versao=str(b.get("termos_versao") or "") or None)


def mascarar(email: str) -> str:
    u, _, d = (email or "").partition("@")
    return (u[:1] + "***@" + d) if d else "***"


def verificar_corrente(ctx, limite: int | None = None) -> dict:
    """Percorre a corrente inteira: recalcula cada hash, confere a ligacao com a anterior e a assinatura."""
    rows = ctx.store.query("SELECT * FROM terms_acceptances ORDER BY id")
    anterior, problemas = "", []
    for r in rows:
        h = _hash_registro(r["email"], r["versao"], r["texto_sha256"], r["aceito_iso"], r["ip"], r["canal"], r["hash_anterior"])
        if r["hash_anterior"] != anterior:
            problemas.append({"id": r["id"], "erro": "ligacao com o registro anterior quebrada"})
        if h != r["hash"]:
            problemas.append({"id": r["id"], "erro": "conteudo alterado (hash nao confere)"})
        if not hmac.compare_digest(_assinar(ctx, r["hash"]), r["assinatura"]):
            problemas.append({"id": r["id"], "erro": "assinatura invalida"})
        anterior = r["hash"]
    return {"ok": not problemas, "registros": len(rows), "problemas": problemas[:50]}


# ------------------------------------------------------------------ rotas

@router.get("/v1/termos")
@_db
async def termos_vigentes(request: Request):
    ctx = request.app.state.ctx
    v = vigente(ctx)
    if v is None:
        raise fail(503, "termos indisponiveis", "service_unavailable", "terms_unavailable")
    return {"versao": v["versao"], "sha256": v["sha256"], "publicado_em": iso(v["publicado_em"]), "texto": v["texto"]}


@router.get("/v1/termos/versao/{versao}")
@_db
async def termos_versao(versao: str, request: Request):
    ctx = request.app.state.ctx
    vigente(ctx)
    v = ctx.store.one("SELECT * FROM terms_versions WHERE versao=?", (versao[:20],))
    if v is None:
        raise fail(404, "versao nao encontrada", "invalid_request_error", "not_found")
    return {"versao": v["versao"], "sha256": v["sha256"], "publicado_em": iso(v["publicado_em"]), "texto": v["texto"]}


@router.get("/v1/termos/verificar")
@_db
async def verificar(request: Request, codigo: str = ""):
    ctx = request.app.state.ctx
    codigo = "".join(ch for ch in codigo.lower() if ch in "0123456789abcdef")
    if len(codigo) < CODIGO_LEN:
        raise fail(400, "informe o codigo do comprovante", "invalid_request_error", "invalid_code")
    from app.accounts_routes import _limit
    if not _limit(request, "tv:" + _ip(request), 30, 600, ctx.clock()):
        raise fail(429, "muitas consultas; aguarde", "rate_limit_error", "too_many_attempts")
    r = ctx.store.one("SELECT * FROM terms_acceptances WHERE hash LIKE ? ORDER BY id LIMIT 1", (codigo + "%",))
    if r is None:
        return {"valido": False}
    h = _hash_registro(r["email"], r["versao"], r["texto_sha256"], r["aceito_iso"], r["ip"], r["canal"], r["hash_anterior"])
    ant = ctx.store.one("SELECT hash FROM terms_acceptances WHERE id<? ORDER BY id DESC LIMIT 1", (r["id"],))
    ok = h == r["hash"] and hmac.compare_digest(_assinar(ctx, r["hash"]), r["assinatura"]) and (r["hash_anterior"] == (ant["hash"] if ant else ""))
    return {"valido": bool(ok), "versao": r["versao"], "aceito_em": r["aceito_iso"], "email": mascarar(r["email"]), "canal": r["canal"], "texto_sha256": r["texto_sha256"]}


@api.get("/termos/aceites")
@_db
def admin_aceites(request: Request, email: str = "", limit: int = 100):
    ctx = require(request)
    lim = max(1, min(limit, 500))
    if email:
        rows = ctx.store.query("SELECT * FROM terms_acceptances WHERE email=? ORDER BY id DESC LIMIT ?", (email.strip().lower(), lim))
    else:
        rows = ctx.store.query("SELECT * FROM terms_acceptances ORDER BY id DESC LIMIT ?", (lim,))
    return {"aceites": [{k: r[k] for k in ("id", "email", "account_id", "versao", "aceito_iso", "ip", "canal", "user_agent")} | {"codigo": r["hash"][:CODIGO_LEN]} for r in rows]}


@api.get("/termos/integridade")
@_db
def admin_integridade(request: Request):
    ctx = require(request)
    return verificar_corrente(ctx)
