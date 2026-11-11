"""Checkout embutido no site (Spec 25 §19.4.0): o site abre a pagina de pagamento da Cakto dentro de um iframe, e o servidor
so acompanha a compra. Fluxo: POST /v1/checkout/sessao (aceite dos termos + cupom) -> {token, url do afiliado se o cupom vale}; o site
abre a Cakto com `callback=<token>`; o webhook `purchase_approved` marca a sessao como `pago` (pelo callback ou, na falta dele, pelo e-mail);
o site consulta GET /v1/checkout/{token} ate ver `pago`. A conta continua nascendo pelo webhook, como sempre."""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Request

from app import accounts as A
from app import affiliates, terms
from app.admin import _db, _ip, _json
from app.ctx import fail

router = APIRouter()

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
JANELA_EMAIL_S = 3600


@router.post("/v1/checkout/sessao")
@_db
async def criar_sessao(request: Request):
    ctx = request.app.state.ctx
    from app.accounts_routes import _limit
    agora = ctx.clock()
    if not _limit(request, "ck:" + _ip(request), 30, 3600, agora):
        raise fail(429, "muitas tentativas; aguarde alguns minutos", "rate_limit_error", "too_many_attempts")
    b = await _json(request)
    email = A.norm_email(b.get("email"))
    if not A.EMAIL_RE.match(email) or len(email) > 200:
        raise fail(400, "Informe um e-mail válido.", "invalid_request_error", "invalid_email")
    produto = str(b.get("produto") or "")
    if not affiliates.produto_valido(produto):
        raise fail(400, "produto invalido", "invalid_request_error", "invalid_product")
    aceite = terms.exigir_aceite(ctx, b, request, email, "checkout")
    aff, url, percentual = None, None, None
    cupom = str(b.get("cupom") or "").strip()
    if cupom:
        a = affiliates.buscar_por_cupom(ctx, cupom)
        if a is not None and a["ativo"]:
            l = ctx.store.one("SELECT * FROM affiliate_links WHERE affiliate_id=? AND produto=?", (a["id"], produto))
            if l is not None:
                aff, url = a, l["url"]
                percentual = affiliates.percentual(affiliates.preco_normal_cents(ctx, produto), l["preco_cents"])
    token = secrets.token_urlsafe(14)
    with ctx.store.tx() as c:
        c.execute("INSERT INTO checkouts(token,email,nome,produto,cupom,affiliate_id,estado,criado_em,ip) VALUES(?,?,?,?,?,?, 'aberto',?,?)",
                  (token, email, str(b.get("nome") or "").strip()[:80], produto, cupom[:40] if aff else "", aff["id"] if aff else None, agora, _ip(request)))
    return {"ok": True, "token": token, "url_afiliado": url, "afiliado": aff["nome"] if aff else None, "percentual": percentual, "aceite": {"versao": aceite["versao"], "codigo": aceite["codigo"]}}


@router.get("/v1/checkout/{token}")
@_db
async def estado(token: str, request: Request):
    ctx = request.app.state.ctx
    from app.accounts_routes import _limit
    if not TOKEN_RE.match(token) or not _limit(request, "ckg:" + _ip(request), 600, 600, ctx.clock()):
        raise fail(404, "sessao nao encontrada", "invalid_request_error", "not_found")
    r = ctx.store.one("SELECT estado, produto, email FROM checkouts WHERE token=?", (token,))
    if r is None:
        raise fail(404, "sessao nao encontrada", "invalid_request_error", "not_found")
    conta = ctx.store.one("SELECT 1 FROM accounts WHERE email=? AND pw_hash IS NOT NULL", (r["email"],)) if r["estado"] == "pago" else None
    return {"estado": r["estado"], "produto": r["produto"], "ja_tem_senha": bool(conta)}


def ao_aprovar(ctx, ev: dict, plan_id: str | None, agora: float) -> None:
    """Chamado pelo processamento do webhook quando uma compra de PLANO foi aprovada: fecha a sessao do checkout e atribui o afiliado."""
    s = None
    if ev.get("callback") and TOKEN_RE.match(ev["callback"]):
        s = ctx.store.one("SELECT * FROM checkouts WHERE token=?", (ev["callback"],))
    if s is None and ev.get("email"):
        s = ctx.store.one("SELECT * FROM checkouts WHERE email=? AND estado='aberto' AND criado_em>=? ORDER BY criado_em DESC LIMIT 1", (ev["email"], agora - JANELA_EMAIL_S))
    if s is not None and s["estado"] != "pago":
        with ctx.store.tx() as c:
            c.execute("UPDATE checkouts SET estado='pago', pago_em=? WHERE id=?", (agora, s["id"]))
    aid, origem, produto = None, "", (s["produto"] if s is not None else (plan_id or ""))
    if s is not None and s["affiliate_id"]:
        aid, origem = s["affiliate_id"], "sessao"
    else:
        a = affiliates.afiliado_por_oferta(ctx, ev.get("offer_id") or "")
        if a is not None:
            aid, origem, produto = a["id"], "oferta", a["produto"]
    if aid:
        affiliates.registrar_venda(ctx, aid, ev.get("email") or "", produto, ev.get("amount_cents"), (ev.get("event_id") or ev.get("external_id") or f"{ev.get('email')}:{agora}") + ":" + str(aid), origem)
