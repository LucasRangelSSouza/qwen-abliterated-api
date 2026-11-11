"""Rotas de contas: webhook da Cakto, login do app, definir senha (publicas) e CRM de assinantes/planos (painel)."""
from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict, deque

from fastapi import APIRouter, Request

from app import accounts as A
from app import credits
from app import mailer
from app.admin import _db, _ip, _json, api, require
from app.auth import sha256
from app.ctx import fail
from app.ledger import iso

router = APIRouter()


def _limit(request: Request, key: str, max_n: int, window_s: float, now: float) -> bool:
    t = getattr(request.app.state, "auth_throttle", None)
    if t is None:  # por instancia do app (nao compartilhado entre processos de teste nem entre apps)
        t = request.app.state.auth_throttle = defaultdict(deque)
    q = t[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= max_n:
        return False
    q.append(now)
    return True


# ---------------------------------------------------------------- webhook da Cakto (publico, so com o segredo)

def _webhook_ok(secret: str, request: Request, raw: bytes, payload: dict) -> bool:
    if not secret:
        return False
    h = request.headers
    candidates = [h.get("x-webhook-secret", ""), h.get("x-cakto-secret", ""), (h.get("authorization", "")[7:] if h.get("authorization", "").lower().startswith("bearer ") else ""),
                  str(payload.get("secret") or payload.get("token") or "")]
    if any(c and hmac.compare_digest(c, secret) for c in candidates):
        return True
    sig = (h.get("x-signature") or h.get("x-cakto-signature") or "").lower().removeprefix("sha256=")
    return bool(sig) and hmac.compare_digest(sig, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest())


@router.post("/v1/billing/cakto")
async def cakto_webhook(request: Request):
    ctx = request.app.state.ctx
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
        assert isinstance(payload, dict)
    except Exception:  # noqa: BLE001
        raise fail(400, "JSON invalido", "invalid_request_error", "invalid_json")
    secret = ctx.s.cakto_webhook_secret
    if not secret:
        raise fail(503, "webhook da Cakto nao configurado", "service_unavailable", "billing_not_configured")
    if not _webhook_ok(secret, request, raw, payload):
        raise fail(401, "segredo do webhook invalido", "invalid_request_error", "invalid_webhook_secret")
    try:
        if isinstance(payload.get("data"), list):  # webhook V2: principal e order bump da mesma cobranca vem juntos, um evento por pedido
            res = [A.process_billing(ctx, "cakto", json.dumps({**payload, "data": d}, ensure_ascii=False), {**payload, "data": d}) for d in payload["data"] if isinstance(d, dict)]
            return {"ok": True, "duplicate": bool(res) and all(r["duplicate"] for r in res), "processed": len(res)}
        return A.process_billing(ctx, "cakto", raw.decode("utf-8", "replace"), payload)
    except RuntimeError as e:  # a Cakto tenta de novo quando recebe erro; o evento ja ficou registrado com o motivo
        raise fail(500, "falha ao processar o evento", "server_error", "billing_processing_failed", detail=str(e)[:120])


# ---------------------------------------------------------------- login do app e senha

def _check_login(ctx, request: Request, email: str, pw: str):
    """Confere e-mail+senha e se a conta pode usar o produto. Devolve (conta, pessoa, assinatura, plano) ou levanta o erro certo."""
    now = ctx.clock()
    if not (_limit(request, "ip:" + _ip(request), 30, 600, now) and _limit(request, "em:" + email, 10, 600, now)):
        raise fail(429, "muitas tentativas; aguarde alguns minutos", "rate_limit_error", "too_many_attempts", headers={"Retry-After": "600"})
    acc = A.find_account(ctx, email)
    if not A.verify_password(pw, acc["pw_hash"] if acc else None) or acc is None:
        raise fail(401, "e-mail ou senha incorretos", "invalid_request_error", "invalid_credentials")
    if acc["status"] != "active":
        raise fail(403, "conta bloqueada; fale com o suporte", "invalid_request_error", "account_blocked")
    A.lapse_sweep(ctx)
    sub = A.current_sub(ctx, acc["id"])
    if not A.sub_allows_use(ctx, sub):
        raise fail(403, "sua assinatura nao esta ativa; renove para continuar", "invalid_request_error", "subscription_inactive")
    person = ctx.store.one("SELECT id,status FROM people WHERE id=?", (acc["person_id"],))
    if person["status"] != "active":
        raise fail(403, "sua conta ainda nao foi liberada; avisaremos assim que estiver pronta", "invalid_request_error", "awaiting_release")
    plan = A.get_plan(ctx, sub["plan"])
    return acc, person, sub, plan


@router.post("/v1/auth/login")
@_db
async def auth_login(request: Request):
    ctx = request.app.state.ctx
    now = ctx.clock()
    b = await _json(request)
    email, pw, device = A.norm_email(b.get("email")), str(b.get("password") or ""), str(b.get("device") or "")
    acc, person, sub, plan = _check_login(ctx, request, email, pw)
    key = A.device_key(ctx, person["id"], device, int(plan["max_devices"] or 0) if plan else 0)
    with ctx.store.tx() as c:
        c.execute("UPDATE accounts SET last_login=? WHERE id=?", (now, acc["id"]))
    lvl = ctx.store.one("SELECT safety_level FROM people WHERE id=?", (person["id"],))
    return {"api_key": key, "safety_level": int(lvl["safety_level"]) if lvl else 1, "account": {"email": acc["email"], "name": acc["name"]}, "plan": {"id": sub["plan"], "name": plan["name"] if plan else sub["plan"], "skill_pack": A.skill_pack(sub["plan"])},
            "next_payment_at": iso(sub["next_payment_at"])}


@router.post("/v1/auth/verify")
@_db
async def auth_verify(request: Request):
    """Confere e-mail+senha SEM emitir chave de maquina (nao gasta o limite de computadores). Usada pelo acesso web remoto."""
    ctx = request.app.state.ctx
    b = await _json(request)
    acc, _person, sub, plan = _check_login(ctx, request, A.norm_email(b.get("email")), str(b.get("password") or ""))
    return {"account_id": acc["id"], "email": acc["email"], "name": acc["name"], "plan": {"id": sub["plan"], "name": plan["name"] if plan else sub["plan"]}}


@router.get("/v1/auth/whoami")
@_db
async def auth_whoami(request: Request):
    """Diz a qual conta pertence a chave de API de um computador (o app se identifica assim na ponte remota)."""
    from app.proxy import authenticate
    ctx = request.app.state.ctx
    p = authenticate(ctx, request)
    acc = ctx.store.one("SELECT id,email,name,status FROM accounts WHERE person_id=?", (p.person_id,)) if p.kind == "person" else None
    if acc is None or acc["status"] != "active":
        raise fail(403, "esta chave nao pertence a uma conta ativa", "invalid_request_error", "no_account")
    sub = A.current_sub(ctx, acc["id"])
    if not A.sub_allows_use(ctx, sub):
        raise fail(403, "sua assinatura nao esta ativa", "invalid_request_error", "subscription_inactive")
    lvl = ctx.store.one("SELECT safety_level FROM people WHERE id=?", (p.person_id,))
    return {"account_id": acc["id"], "email": acc["email"], "name": acc["name"], "safety_level": int(lvl["safety_level"]) if lvl else 1,
            "plan": {"id": sub["plan"], "skill_pack": A.skill_pack(sub["plan"])},
            "termos_aceitos": __import__("app.terms", fromlist=["aceitou"]).aceitou(ctx, acc["email"], acc["id"])}


@router.get("/v1/credits")
@_db
async def client_credits(request: Request):
    """O que o pop-up de limite estourado precisa (Spec 24): saldos de credito, pacotes de R$ 100 com o checkout ja com o e-mail do cliente e o proximo plano."""
    from urllib.parse import quote
    from app.proxy import authenticate
    ctx = request.app.state.ctx
    p = authenticate(ctx, request)
    acc = ctx.store.one("SELECT id,email FROM accounts WHERE person_id=?", (p.person_id,)) if p.kind == "person" else None
    if acc is None:
        raise fail(403, "esta chave nao pertence a uma conta", "invalid_request_error", "no_account")
    now = ctx.clock()
    sub = A.current_sub(ctx, acc["id"])
    plan_id = sub["plan"] if sub else None
    nxt = credits.NEXT_PLAN.get(plan_id or "")
    nxt_row = ctx.store.one("SELECT id,name FROM plans WHERE id=?", (nxt,)) if nxt else None
    site = mailer.site_url()
    session_limit = ctx.ledger.limits(p.person_id)["session_limit_tokens"]
    ofertas = credits.offers(ctx.store)
    packs = []
    for kind in credits.KINDS:
        units = credits.units_for_pack(ctx.store, kind, session_limit)
        if not units:
            continue  # plano sem limite de uso nao compra credito de uso
        url = f"https://pay.cakto.com.br/{ofertas[kind]}?email={quote(acc['email'])}" if kind in ofertas else None
        packs.append({"kind": kind, "label": credits.LABEL[kind], "price_usd": credits.price_usd(ctx.store), "currency": "USD", "units": units, "expires_days": int(credits.expiry_days(ctx.store)), "checkout_url": url})
    url_prox = "https://wa.me/5562985613482" if nxt == "enterprise" else f"{site}/#planos"  # Enterprise e venda consultiva
    return {"plan": plan_id, "next_plan": ({"id": nxt_row["id"], "name": nxt_row["name"], "url": url_prox} if nxt_row else None),
            "balances": credits.balances(ctx.store, p.person_id, now), "packs": packs}


@router.post("/v1/auth/set-password")
@_db
async def auth_set_password(request: Request):
    ctx = request.app.state.ctx
    now = ctx.clock()
    if not _limit(request, "sp:" + _ip(request), 20, 600, now):
        raise fail(429, "muitas tentativas; aguarde alguns minutos", "rate_limit_error", "too_many_attempts", headers={"Retry-After": "600"})
    b = await _json(request)
    problema = A.weak_password(str(b.get("password") or ""))
    if problema:
        raise fail(422, problema, "invalid_request_error", "weak_password")
    acc = ctx.store.one("SELECT * FROM accounts WHERE setup_hash=?", (sha256(str(b.get("token") or "")),))
    if acc is None or (acc["setup_expires"] or 0) < now:
        raise fail(400, "link invalido ou vencido; peca um novo", "invalid_request_error", "invalid_token")
    from app import terms
    if acc["last_login"] is None and terms.aceitou(ctx, acc["email"], acc["id"]) is None:  # conta nova: o aceite dos termos e obrigatorio (recuperar senha de conta antiga nao pede)
        terms.exigir_aceite(ctx, b, request, acc["email"], "site", account_id=acc["id"])
    elif b.get("aceite_termos"):
        terms.exigir_aceite(ctx, b, request, acc["email"], "site", account_id=acc["id"])
    with ctx.store.tx() as c:
        c.execute("UPDATE accounts SET pw_hash=?, setup_hash=NULL, setup_expires=NULL WHERE id=?", (A.hash_password(str(b["password"])), acc["id"]))
    try:
        # o e-mail depois da senha muda conforme o plano e a maquina (Spec 25 §11.1)
        vm = ctx.store.one("SELECT status FROM vms WHERE account_id=? AND status NOT IN ('canceled','error') ORDER BY id DESC LIMIT 1", (acc["id"],))
        sub = ctx.store.one("SELECT plan FROM subscriptions WHERE account_id=? ORDER BY id DESC LIMIT 1", (acc["id"],))
        plano = A.get_plan(ctx, sub["plan"]) if sub else None
        recuperacao = acc["last_login"] is not None  # ja usou a conta: so redefiniu a senha
        if vm is not None and vm["status"] != "running":  # comprou maquina virtual que ainda nao ficou pronta: explica o espaco exclusivo
            mailer.send_vm_preparing(ctx, acc["email"], acc["name"] or "")
        elif vm is not None and not recuperacao:  # maquina pronta: entra direto nela pelo remoto
            mailer.send_vm_ready(ctx, acc["email"], acc["name"] or "")
        else:  # sem maquina: baixar o aplicativo e entrar (ou a versao curta de quem so redefiniu a senha)
            mailer.send_password_done(ctx, acc["email"], acc["name"] or "", plano=(plano["name"] if plano else None), recuperacao=recuperacao)
    except Exception:  # noqa: BLE001  e-mail nunca derruba a troca de senha
        pass
    return {"ok": True}


@router.post("/v1/auth/forgot")
@_db
async def auth_forgot(request: Request):
    """Sempre responde igual (nao revela se o e-mail existe). Com SMTP configurado manda o link; sem SMTP o admin gera o link no painel."""
    ctx = request.app.state.ctx
    if not _limit(request, "fg:" + _ip(request), 10, 3600, ctx.clock()):
        raise fail(429, "muitas tentativas; tente mais tarde", "rate_limit_error", "too_many_attempts", headers={"Retry-After": "3600"})
    acc = A.find_account(ctx, str((await _json(request)).get("email") or ""))
    if acc is not None and acc["status"] == "active":
        tok = A.new_setup_token(ctx, acc["id"])
        mailer.send_reset(ctx, acc["email"], tok)
    return {"ok": True}


# ---------------------------------------------------------------- CRM (painel)

STATUS_LABEL = {"active": "Assinando", "late": "Em atraso", "canceled": "Cancelado", "refunded": "Reembolsado", "chargeback": "Chargeback", "pending": "Pendente", None: "Sem assinatura"}

_SUB_SQL = ("SELECT a.id, a.email, a.name, a.status acc_status, a.person_id, a.created_at, a.last_login, (a.pw_hash IS NOT NULL) has_password,"
            " p.status person_status, p.safety_level,"
            " s.plan, s.status sub_status, s.next_payment_at, s.amount_cents, s.blocked_by_billing,"
            " (SELECT COUNT(*) FROM keys k WHERE k.person_id=a.person_id AND k.revoked_at IS NULL AND k.label LIKE 'app:%') devices"
            " FROM accounts a JOIN people p ON p.id=a.person_id"
            " LEFT JOIN subscriptions s ON s.id=(SELECT s2.id FROM subscriptions s2 WHERE s2.account_id=a.id ORDER BY s2.updated_at DESC, s2.id DESC LIMIT 1)")


def _sub_view(r) -> dict:
    return {"id": r["id"], "email": r["email"], "name": r["name"], "plan": r["plan"], "status": r["sub_status"], "status_label": STATUS_LABEL.get(r["sub_status"], r["sub_status"]),
            "next_payment_at": iso(r["next_payment_at"]), "amount_cents": r["amount_cents"], "enabled": r["person_status"] == "active", "blocked_by_billing": bool(r["blocked_by_billing"]),
            "blocked": r["acc_status"] != "active", "safety_level": r["safety_level"], "devices": r["devices"], "has_password": bool(r["has_password"]), "person_id": r["person_id"],
            "created_at": iso(r["created_at"]), "last_login": iso(r["last_login"])}


def _one(ctx, sid: int) -> dict:
    r = ctx.store.one(_SUB_SQL + " WHERE a.id=?", (sid,))
    if r is None:
        raise fail(404, "assinante nao encontrado", "invalid_request_error", "not_found")
    return _sub_view(r)


@api.get("/subscribers")
@_db
def subscribers_list(request: Request, q: str = "", status: str = ""):
    ctx = require(request)
    A.lapse_sweep(ctx)
    rows = ctx.store.query(_SUB_SQL + " ORDER BY a.created_at DESC")
    out = [_sub_view(r) for r in rows]
    summary = {"total": len(out), "active": sum(1 for x in out if x["status"] == "active"), "late": sum(1 for x in out if x["status"] == "late"),
               "ended": sum(1 for x in out if x["status"] in ("canceled", "refunded", "chargeback")), "awaiting": sum(1 for x in out if x["status"] == "active" and not x["enabled"]),
               "mrr_cents": sum(x["amount_cents"] or 0 for x in out if x["status"] == "active")}
    ql = q.strip().lower()
    if ql:
        out = [x for x in out if ql in (x["email"] or "").lower() or ql in (x["name"] or "").lower()]
    if status:
        out = [x for x in out if (x["status"] or "none") == status or (status == "awaiting" and x["status"] == "active" and not x["enabled"])]
    return {"summary": summary, "rows": out, "smtp": mailer.configured()}


@api.post("/subscribers")
@_db
async def subscribers_create(request: Request):
    """Cadastro manual (teste, cortesia ou venda fora da Cakto): cria conta + pessoa desabilitada e devolve o link de senha."""
    ctx = require(request)
    b = await _json(request)
    email = A.norm_email(b.get("email"))
    if not A.EMAIL_RE.match(email):
        raise fail(422, "e-mail invalido", "invalid_request_error", "invalid_email")
    if A.find_account(ctx, email):
        raise fail(409, "ja existe uma conta com este e-mail", "invalid_request_error", "email_taken")
    plan_id = str(b.get("plan") or "light")
    if A.get_plan(ctx, plan_id) is None:
        raise fail(422, "plano desconhecido", "invalid_request_error", "invalid_plan")
    lvl = b.get("safety_level")
    acc, tok = A.create_account(ctx, email, str(b.get("name") or ""), plan_id, lvl if lvl in (1, 2) else None)
    now = ctx.clock()
    with ctx.store.tx() as c:
        c.execute("INSERT INTO subscriptions(account_id,plan,status,provider,external_id,next_payment_at,amount_cents,started_at,updated_at,blocked_by_billing) VALUES(?,?,?,?,?,?,?,?,?,0)",
                  (acc["id"], plan_id, "active" if b.get("active", True) else "pending", "manual", None, None, None, now, now))
    A.apply_plan_limits(ctx, acc["person_id"], A.get_plan(ctx, plan_id))
    if b.get("enable"):
        A.set_person_status(ctx, acc["person_id"], "active")
    return {"subscriber": _one(ctx, acc["id"]), "setup_link": mailer.setup_link(tok), "email_sent": mailer.send_welcome(ctx, email, str(b.get("name") or ""), tok, liberada=False)}


@api.delete("/subscribers/{sid}")
@_db
def subscribers_delete(sid: int, request: Request):
    """Remove uma conta (cliente de teste ou encerrado): apaga assinaturas e pedidos de VM, revoga as chaves e suspende a pessoa
    (o historico de uso continua ligado a ela). Recusa se houver VM viva na Contabo: cancele a VM antes."""
    ctx = require(request)
    acc = ctx.store.one("SELECT id,person_id,email FROM accounts WHERE id=?", (sid,))
    if acc is None:
        raise fail(404, "assinante nao encontrado", "invalid_request_error", "not_found")
    viva = ctx.store.one("SELECT id FROM vms WHERE account_id=? AND contabo_id IS NOT NULL AND status!='canceled'", (sid,))
    if viva:
        raise fail(409, "este assinante ainda tem uma maquina virtual ativa na Contabo; cancele a VM antes de excluir", "invalid_request_error", "vm_active")
    with ctx.store.tx() as c:
        c.execute("DELETE FROM vms WHERE account_id=?", (sid,))
        c.execute("DELETE FROM subscriptions WHERE account_id=?", (sid,))
        c.execute("UPDATE keys SET revoked_at=? WHERE person_id=? AND revoked_at IS NULL", (ctx.clock(), acc["person_id"]))
        c.execute("DELETE FROM accounts WHERE id=?", (sid,))
    A.set_person_status(ctx, acc["person_id"], "suspended")
    return {"ok": True}


def _set_enabled(ctx, sid: int, enabled: bool) -> dict:
    acc = ctx.store.one("SELECT person_id FROM accounts WHERE id=?", (sid,))
    if acc is None:
        raise fail(404, "assinante nao encontrado", "invalid_request_error", "not_found")
    A.set_person_status(ctx, acc["person_id"], "active" if enabled else "suspended")
    with ctx.store.tx() as c:  # liberar/bloquear na mao substitui o bloqueio automatico por pagamento
        c.execute("UPDATE subscriptions SET blocked_by_billing=0 WHERE account_id=?", (sid,))
    return _one(ctx, sid)


@api.post("/subscribers/{sid}/enable")
@_db
async def subscribers_enable(sid: int, request: Request):
    return _set_enabled(require(request), sid, True)


@api.post("/subscribers/{sid}/disable")
@_db
async def subscribers_disable(sid: int, request: Request):
    return _set_enabled(require(request), sid, False)


@api.post("/subscribers/{sid}/password-link")
@_db
async def subscribers_password_link(sid: int, request: Request):
    ctx = require(request)
    _one(ctx, sid)
    return {"setup_link": mailer.setup_link(A.new_setup_token(ctx, sid))}


@api.patch("/subscribers/{sid}")
@_db
async def subscribers_patch(sid: int, request: Request):
    ctx = require(request)
    b = await _json(request)
    cur = _one(ctx, sid)
    if "name" in b:
        with ctx.store.tx() as c:
            c.execute("UPDATE accounts SET name=? WHERE id=?", (str(b["name"])[:80], sid))
    if "blocked" in b:
        with ctx.store.tx() as c:
            c.execute("UPDATE accounts SET status=? WHERE id=?", ("blocked" if b["blocked"] else "active", sid))
    if "plan" in b:
        plan = A.get_plan(ctx, str(b["plan"]))
        if plan is None:
            raise fail(422, "plano desconhecido", "invalid_request_error", "invalid_plan")
        with ctx.store.tx() as c:
            c.execute("UPDATE subscriptions SET plan=?, updated_at=? WHERE account_id=? AND id=(SELECT MAX(id) FROM subscriptions WHERE account_id=?)", (plan["id"], ctx.clock(), sid, sid))
        A.apply_plan_limits(ctx, cur and ctx.store.one("SELECT person_id FROM accounts WHERE id=?", (sid,))["person_id"], plan)
    return _one(ctx, sid)


@api.get("/plans")
@_db
def plans_list(request: Request):
    ctx = require(request)
    A.ensure_plans(ctx)
    return {"plans": [dict(r) for r in ctx.store.query("SELECT * FROM plans ORDER BY CASE WHEN price_cents=0 THEN 1 ELSE 0 END, price_cents")]}


@api.put("/plans/{pid}")
@_db
async def plans_put(pid: str, request: Request):
    ctx = require(request)
    b = await _json(request)
    if A.get_plan(ctx, pid) is None:
        raise fail(404, "plano nao encontrado", "invalid_request_error", "not_found")
    cols = {"name": str, "session_limit_tokens": int, "week_limit_tokens": int, "image_quota_month": int, "video_quota_month": int, "audio_quota_month": int, "safety_level": int, "max_devices": int, "price_cents": int}
    extra = set(b) - set(cols)
    if extra:
        raise fail(422, "campos desconhecidos: " + ", ".join(sorted(extra)), "invalid_request_error", "invalid_plan")
    nullable = {"session_limit_tokens", "week_limit_tokens"}  # null = sem limite
    with ctx.store.tx() as c:
        for k, v in b.items():
            if v is None and k in nullable:
                c.execute(f"UPDATE plans SET {k}=NULL WHERE id=?", (pid,))
                continue
            if cols[k] is int and (isinstance(v, bool) or not isinstance(v, int) or v < 0 or (k == "safety_level" and v not in (1, 2))):  # max_devices 0 = ilimitado
                raise fail(422, f"{k} invalido", "invalid_request_error", "invalid_plan")
            if cols[k] is str and not (isinstance(v, str) and v.strip()):
                raise fail(422, f"{k} invalido", "invalid_request_error", "invalid_plan")
            c.execute(f"UPDATE plans SET {k}=? WHERE id=?", (v.strip() if isinstance(v, str) else v, pid))
    return dict(A.get_plan(ctx, pid))


@api.get("/billing/settings")
@_db
def billing_settings(request: Request):
    ctx = require(request)
    raw = ctx.store.get_setting("billing_plan_map")
    return {"grace_days": A.grace_days(ctx), "auto_enable": A.auto_enable(ctx), "plan_map": json.loads(raw) if raw else {}, "webhook_configured": bool(ctx.s.cakto_webhook_secret),
            "smtp_configured": mailer.configured(), "site_url": mailer.site_url(),
            "credit_offers": credits.offers(ctx.store), "credit_products": credits._json_setting(ctx.store, "credit_products"),
            "credit_pack_units": credits._json_setting(ctx.store, "credit_pack_units"), "credit_expiry_days": credits.expiry_days(ctx.store),
            "usd_brl_rate": credits.usd_brl_rate(ctx.store), "credit_price_usd": credits.price_usd(ctx.store)}


@api.put("/billing/settings")
@_db
async def billing_settings_put(request: Request):
    ctx = require(request)
    b = await _json(request)
    if "grace_days" in b:
        if isinstance(b["grace_days"], bool) or not isinstance(b["grace_days"], int) or not 0 <= b["grace_days"] <= 60:
            raise fail(422, "grace_days deve ser de 0 a 60", "invalid_request_error", "invalid_billing_settings")
        ctx.store.set_setting("billing_grace_days", str(b["grace_days"]))
    if "auto_enable" in b:
        if not isinstance(b["auto_enable"], bool):
            raise fail(422, "auto_enable deve ser true ou false", "invalid_request_error", "invalid_billing_settings")
        ctx.store.set_setting("billing_auto_enable", "1" if b["auto_enable"] else "0")
    if "plan_map" in b:
        if not isinstance(b["plan_map"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in b["plan_map"].items()):
            raise fail(422, "plan_map deve ser {trecho do nome: id do plano}", "invalid_request_error", "invalid_billing_settings")
        ctx.store.set_setting("billing_plan_map", json.dumps(b["plan_map"], ensure_ascii=False))
    for key in ("credit_offers", "credit_products"):  # {tipo: id da oferta/produto na Cakto}
        if key in b:
            v = b[key]
            if not isinstance(v, dict) or any(k not in credits.KINDS or not isinstance(x, str) for k, x in v.items()):
                raise fail(422, f"{key} deve ser {{tokens|image|video|audio: texto}}", "invalid_request_error", "invalid_billing_settings")
            ctx.store.set_setting(key, json.dumps(v, ensure_ascii=False))
    if "credit_pack_units" in b:
        v = b["credit_pack_units"]
        if not isinstance(v, dict) or any(k not in (*credits.KINDS, "tokens_mult") or isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 for k, x in v.items()):
            raise fail(422, "credit_pack_units deve ser {image|video|audio|tokens_mult: numero > 0}", "invalid_request_error", "invalid_billing_settings")
        ctx.store.set_setting("credit_pack_units", json.dumps(v))
    for key in ("usd_brl_rate", "credit_price_usd"):  # moeda: cotacao de referencia e preco do pacote em dolar (vazio = calcula pela cotacao)
        if key in b:
            v = b[key]
            if v is None and key == "credit_price_usd":
                ctx.store.set_setting(key, "")
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= (20 if key == "usd_brl_rate" else 100000):
                raise fail(422, f"{key} invalido", "invalid_request_error", "invalid_billing_settings")
            ctx.store.set_setting(key, str(v))
    if "credit_expiry_days" in b:
        v = b["credit_expiry_days"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= 365:
            raise fail(422, "credit_expiry_days deve ser de 1 a 365", "invalid_request_error", "invalid_billing_settings")
        ctx.store.set_setting("credit_expiry_days", str(v))
    return billing_settings(request)


@api.get("/people/{pid}/credits")
@_db
def person_credits(pid: int, request: Request):
    ctx = require(request)
    now = ctx.clock()
    return {"balances": credits.balances(ctx.store, pid, now), "rows": credits.listing(ctx.store, pid, now)}


@api.post("/people/{pid}/credits")
@_db
async def person_credits_grant(pid: int, request: Request):
    """Concede um pacote na mao (compra que nao veio pelo webhook, cortesia). units omitido = o tamanho do pacote de R$ 100."""
    ctx = require(request)
    b = await _json(request)
    kind = b.get("kind")
    if kind not in credits.KINDS:
        raise fail(422, "kind deve ser tokens, image, video ou audio", "invalid_request_error", "invalid_credit")
    if ctx.store.one("SELECT id FROM people WHERE id=?", (pid,)) is None:
        raise fail(404, "pessoa nao encontrada", "invalid_request_error", "not_found")
    units = b.get("units")
    if units is None:
        units = credits.units_for_pack(ctx.store, kind, ctx.ledger.limits(pid)["session_limit_tokens"])
    if isinstance(units, bool) or not isinstance(units, int) or units <= 0:
        raise fail(422, "units deve ser inteiro > 0", "invalid_request_error", "invalid_credit")
    cid = credits.grant(ctx.store, pid, kind, units, ctx.clock(), "admin")
    return {"id": cid, "kind": kind, "units": units}


@api.delete("/credits/{cid}")
@_db
def credit_revoke(cid: int, request: Request):
    ctx = require(request)
    if not credits.revoke(ctx.store, cid, ctx.clock()):
        raise fail(404, "pacote nao encontrado ou ja revogado", "invalid_request_error", "not_found")
    return {"ok": True}


@api.get("/billing/events")
@_db
def billing_events(request: Request, limit: int = 30):
    ctx = require(request)
    rows = ctx.store.query("SELECT id,provider,kind,received_at,processed,error FROM billing_events ORDER BY received_at DESC, id DESC LIMIT ?", (max(1, min(limit, 200)),))
    return {"rows": [{"id": r["id"], "provider": r["provider"], "kind": r["kind"], "at": iso(r["received_at"]), "processed": bool(r["processed"]), "error": r["error"]} for r in rows]}
