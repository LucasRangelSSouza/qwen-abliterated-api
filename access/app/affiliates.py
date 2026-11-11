"""Afiliados e cupom de desconto (Spec 25 §19.2-§19.3).

Cada afiliado tem um cupom (sem validade, liga/desliga) e, por produto, o link de checkout DELE na Cakto (oferta com preco menor) e o preco com desconto.
O cupom so vale para os produtos que tem link. Creditos avulsos nunca entram. O percentual e calculado: 1 - preco / preco normal."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, Request

from app.admin import _db, _ip, _json, api, require
from app.ctx import fail
from app.ledger import iso

router = APIRouter()

PLANOS = ("lite", "max", "ultra")
# chaves de produto: <plano>_<ciclo>[_vm] e a maquina avulsa
PRODUTOS = tuple(f"{p}_{c}{v}" for p in PLANOS for c in ("mensal", "anual") for v in ("", "_vm")) + ("vm_mensal", "vm_anual")
ROTULO = {"lite": "Lite", "max": "Max", "ultra": "Ultra", "vm": "Máquina virtual"}
MESES_ANUAL = 10  # anual = 10 vezes o mensal
CUPOM_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")


def produto_valido(p: str) -> bool:
    return p in PRODUTOS


def preco_normal_cents(ctx, produto: str) -> int | None:
    """Preco normal do produto, no ciclo dele: plano (tabela plans) + maquina (vm_price_cents); anual = 10 meses."""
    if not produto_valido(produto):
        return None
    from app import accounts as A
    A.ensure_plans(ctx)  # garante os planos padrao (idempotente)
    parts = produto.split("_")
    plano, ciclo, vm = parts[0], parts[1], produto.endswith("_vm")
    total = 0
    if plano != "vm":
        pid = "light" if plano == "lite" else plano
        r = ctx.store.one("SELECT price_cents FROM plans WHERE id=?", (pid,))
        if r is None or not r["price_cents"]:
            return None
        total += int(r["price_cents"])
    if vm or plano == "vm":
        total += int(ctx.store.get_setting("vm_price_cents") or 39900)
    return total * (MESES_ANUAL if ciclo == "anual" else 1)


def percentual(normal: int | None, preco: int) -> int | None:
    if not normal or normal <= 0:
        return None
    return max(0, round((1 - preco / normal) * 100))


def tirar_ids(url: str) -> tuple[str, str]:
    """(oferta, afiliado) tirados do link da Cakto: pay.cakto.com.br/<oferta>?affiliate=<short_id>. Vazio se nao der."""
    try:
        u = urlparse(url)
        oferta = u.path.strip("/").split("/")[0] if u.path.strip("/") else ""
        from urllib.parse import parse_qs
        q = parse_qs(u.query)
        afil = (q.get("affiliate") or q.get("aff") or q.get("a") or q.get("ref") or [""])[0]
        return oferta[:60], afil[:60]
    except Exception:  # noqa: BLE001
        return "", ""


def buscar_por_cupom(ctx, cupom: str):
    return ctx.store.one("SELECT * FROM affiliates WHERE LOWER(cupom)=?", ((cupom or "").strip().lower(),))


def links_de(ctx, affiliate_id: int) -> list:
    return ctx.store.query("SELECT * FROM affiliate_links WHERE affiliate_id=? ORDER BY produto", (affiliate_id,))


def afiliado_por_oferta(ctx, oferta_id: str):
    """Afiliado dono de uma oferta da Cakto (para atribuir a venda quando o webhook so traz a oferta)."""
    if not oferta_id:
        return None
    return ctx.store.one("SELECT a.*, l.produto AS produto FROM affiliate_links l JOIN affiliates a ON a.id=l.affiliate_id WHERE l.cakto_offer_id=? LIMIT 1", (oferta_id,))


def registrar_venda(ctx, affiliate_id: int, email: str, produto: str, amount_cents: int | None, event_id: str, origem: str) -> bool:
    """Grava a venda do afiliado uma vez por evento. So conta a PRIMEIRA venda de plano do e-mail (renovacao nao entra)."""
    ja = ctx.store.one("SELECT 1 FROM affiliate_sales WHERE email=? AND email<>''", (email,))
    if ja is not None:
        return False
    try:
        with ctx.store.tx() as c:
            c.execute("INSERT INTO affiliate_sales(affiliate_id,email,produto,amount_cents,event_id,origem,at) VALUES(?,?,?,?,?,?,?)", (affiliate_id, email, produto, amount_cents, event_id[:120], origem[:20], ctx.clock()))
        return True
    except Exception:  # noqa: BLE001  evento repetido
        return False


# ------------------------------------------------------------------ publico: consultar o cupom

@router.get("/v1/cupom/{codigo}")
@_db
async def consultar_cupom(codigo: str, request: Request):
    ctx = request.app.state.ctx
    from app.accounts_routes import _limit
    if not _limit(request, "cp:" + _ip(request), 40, 600, ctx.clock()):
        raise fail(429, "muitas tentativas; aguarde alguns minutos", "rate_limit_error", "too_many_attempts")
    a = buscar_por_cupom(ctx, codigo) if CUPOM_RE.match(codigo or "") else None
    if a is None:
        return {"ok": False, "motivo": "nao_encontrado", "mensagem": "Cupom não encontrado."}
    if not a["ativo"]:
        return {"ok": False, "motivo": "inativo", "mensagem": "Este cupom não está disponível no momento."}
    prods = {}
    for l in links_de(ctx, a["id"]):
        normal = preco_normal_cents(ctx, l["produto"])
        prods[l["produto"]] = {"url": l["url"], "preco": l["preco_cents"] / 100, "preco_normal": (normal / 100) if normal else None, "percentual": percentual(normal, l["preco_cents"])}
    if not prods:
        return {"ok": False, "motivo": "sem_produtos", "mensagem": "Este cupom não vale para nenhum plano no momento."}
    maior = max((p["percentual"] or 0) for p in prods.values())
    return {"ok": True, "afiliado": a["nome"], "cupom": a["cupom"], "percentual": maior, "produtos": prods}


# ------------------------------------------------------------------ painel

def _linha(ctx, a) -> dict:
    vendas = ctx.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS total FROM affiliate_sales WHERE affiliate_id=?", (a["id"],))
    mes = ctx.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS total FROM affiliate_sales WHERE affiliate_id=? AND at>=?", (a["id"], ctx.clock() - 30 * 86400))
    links = []
    for l in links_de(ctx, a["id"]):
        normal = preco_normal_cents(ctx, l["produto"])
        links.append({"produto": l["produto"], "url": l["url"], "preco": l["preco_cents"] / 100, "preco_normal": (normal / 100) if normal else None, "percentual": percentual(normal, l["preco_cents"]),
                      "cakto_offer_id": l["cakto_offer_id"], "cakto_affiliate_id": l["cakto_affiliate_id"]})
    return {"id": a["id"], "nome": a["nome"], "cupom": a["cupom"], "ativo": bool(a["ativo"]), "observacoes": a["observacoes"], "criado_em": iso(a["criado_em"]),
            "vendas": vendas["n"], "vendas_valor": vendas["total"] / 100, "vendas_30d": mes["n"], "vendas_30d_valor": mes["total"] / 100, "links": links}


@api.get("/afiliados")
@_db
def listar(request: Request):
    ctx = require(request)
    return {"afiliados": [_linha(ctx, a) for a in ctx.store.query("SELECT * FROM affiliates ORDER BY ativo DESC, LOWER(nome)")], "produtos": [{"id": p, "rotulo": rotulo(p), "preco_normal": (preco_normal_cents(ctx, p) or 0) / 100} for p in PRODUTOS]}


def rotulo(p: str) -> str:
    pl, ciclo = p.split("_")[0], p.split("_")[1]
    return f"{ROTULO[pl]} {ciclo}" + (" + máquina" if p.endswith("_vm") and pl != "vm" else "")


def _preco(v) -> int:
    try:
        n = round(float(str(v).replace(",", ".")) * 100)
    except (TypeError, ValueError):
        raise fail(400, "preco invalido", "invalid_request_error", "invalid_price")
    if n <= 0 or n > 10_000_000:
        raise fail(400, "preco invalido", "invalid_request_error", "invalid_price")
    return n


def _gravar_links(ctx, c, aid: int, links) -> None:
    c.execute("DELETE FROM affiliate_links WHERE affiliate_id=?", (aid,))
    vistos = set()
    for l in links or []:
        prod = str(l.get("produto") or "")
        url = str(l.get("url") or "").strip()
        if not url and not l.get("preco"):
            continue  # linha em branco: o afiliado nao tem este produto
        if not produto_valido(prod) or prod in vistos:
            raise fail(400, f"produto invalido: {prod}", "invalid_request_error", "invalid_product")
        if not re.match(r"^https://", url):
            raise fail(400, f"o link de {prod} precisa comecar com https://", "invalid_request_error", "invalid_url")
        vistos.add(prod)
        oferta, afil = tirar_ids(url)
        oferta = str(l.get("cakto_offer_id") or oferta)[:60]
        afil = str(l.get("cakto_affiliate_id") or afil)[:60]
        c.execute("INSERT INTO affiliate_links(affiliate_id,produto,url,preco_cents,cakto_offer_id,cakto_affiliate_id) VALUES(?,?,?,?,?,?)", (aid, prod, url[:500], _preco(l.get("preco")), oferta, afil))


@api.post("/afiliados")
@_db
async def criar(request: Request):
    ctx = require(request)
    b = await _json(request)
    nome, cupom = str(b.get("nome") or "").strip()[:80], str(b.get("cupom") or "").strip()
    if not nome or not CUPOM_RE.match(cupom):
        raise fail(400, "informe o nome e um cupom (2 a 40 letras, numeros, - ou _)", "invalid_request_error", "invalid_affiliate")
    if buscar_por_cupom(ctx, cupom) is not None:
        raise fail(409, "ja existe um afiliado com esse cupom", "invalid_request_error", "coupon_exists")
    agora = ctx.clock()
    with ctx.store.tx() as c:
        cur = c.execute("INSERT INTO affiliates(nome,cupom,ativo,observacoes,criado_em,atualizado_em) VALUES(?,?,?,?,?,?)", (nome, cupom, 1 if b.get("ativo", True) else 0, str(b.get("observacoes") or "")[:500], agora, agora))
        aid = cur.lastrowid
        _gravar_links(ctx, c, aid, b.get("links"))
    return _linha(ctx, ctx.store.one("SELECT * FROM affiliates WHERE id=?", (aid,)))


@api.put("/afiliados/{aid}")
@_db
async def editar(aid: int, request: Request):
    ctx = require(request)
    a = ctx.store.one("SELECT * FROM affiliates WHERE id=?", (aid,))
    if a is None:
        raise fail(404, "afiliado nao encontrado", "invalid_request_error", "not_found")
    b = await _json(request)
    nome = str(b.get("nome", a["nome"])).strip()[:80]
    cupom = str(b.get("cupom", a["cupom"])).strip()
    if not nome or not CUPOM_RE.match(cupom):
        raise fail(400, "informe o nome e um cupom (2 a 40 letras, numeros, - ou _)", "invalid_request_error", "invalid_affiliate")
    outro = buscar_por_cupom(ctx, cupom)
    if outro is not None and outro["id"] != aid:
        raise fail(409, "ja existe um afiliado com esse cupom", "invalid_request_error", "coupon_exists")
    with ctx.store.tx() as c:
        c.execute("UPDATE affiliates SET nome=?, cupom=?, ativo=?, observacoes=?, atualizado_em=? WHERE id=?",
                  (nome, cupom, (1 if b["ativo"] else 0) if "ativo" in b else a["ativo"], str(b.get("observacoes", a["observacoes"]))[:500], ctx.clock(), aid))
        if "links" in b:
            _gravar_links(ctx, c, aid, b["links"])
    return _linha(ctx, ctx.store.one("SELECT * FROM affiliates WHERE id=?", (aid,)))


@api.get("/afiliados/{aid}/vendas")
@_db
def vendas(aid: int, request: Request):
    ctx = require(request)
    rows = ctx.store.query("SELECT * FROM affiliate_sales WHERE affiliate_id=? ORDER BY at DESC LIMIT 200", (aid,))
    return {"vendas": [{"email": r["email"], "produto": r["produto"], "valor": (r["amount_cents"] or 0) / 100, "origem": r["origem"], "quando": iso(r["at"])} for r in rows]}
