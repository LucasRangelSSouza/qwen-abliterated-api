"""Contas, assinaturas e login (Spec 21 §7). Tudo no mesmo banco do llm-access (Postgres unificado em producao).

Modelo:
- `accounts`: quem faz login (e-mail + senha). Aponta para uma pessoa (`people`), que carrega limites, nivel de seguranca e as chaves.
- `subscriptions`: o plano e o estado do pagamento (vindo do webhook da Cakto). A Cakto e a fonte da verdade; aqui e so espelho.
- `billing_events`: todo webhook recebido, para idempotencia e auditoria.
- A chave de API NAO e guardada em texto (so o hash). No login o servidor emite UMA CHAVE NOVA por computador (ate `max_devices`).
- "Habilitar" = retomar a pessoa vinculada (`people.status`). Quem compra nasce desabilitado ate o administrador liberar.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone

from app.auth import new_api_key, sha256
from app.ctx import Ctx

DEFAULT_PLANS = [  # editaveis no painel. tokens None = sem limite; max_devices 0 = computadores ilimitados; safety_level = padrao da pessoa nova
    # id, nome, tokens/sessao (5 h), tokens/semana, imagens/mes, nivel de seguranca, computadores, preco (centavos), videos/mes, audios/mes
    # uso em multiplos do Lite (1x): Max 5x, Ultra 20x. Cota de midia por mes (video em clipes de 5 s 480p; 720p conta 3): GPU <= 25% da mensalidade (docs/PRECIFICACAO_MIDIA.md §7)
    ("light", "Lite", 1_000_000, 10_000_000, 100, 2, 0, 11900, 5, 20),
    ("max", "Max", 5_000_000, 50_000_000, 1000, 1, 0, 49700, 30, 150),
    ("ultra", "Ultra", 20_000_000, 200_000_000, 4000, 1, 0, 119700, 100, 500),
    ("enterprise", "Enterprise", None, None, 10000, 1, 0, 0, 300, 3000),  # sob contrato (contato pelo WhatsApp): o administrador atribui, nao ha checkout
]
LEGACY_PLANS = ("pro", "infinity")  # nomes antigos: quem ja assina continua valendo; "pro" virou "light" (migrado em ensure_plans)
PLANS_WITHOUT_SKILL_PACK = ("light", "pro")  # o pacote de skills profissionais nao vai no plano Light (o instalador e o mesmo para todos)
SUB_STATUS = ("pending", "active", "late", "canceled", "refunded", "chargeback")
GRACE_DAYS_DEFAULT = 3
SETUP_TTL_S = 7 * 86400


# ---------------------------------------------------------------- senha (scrypt, so stdlib)

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, h = (stored or "").split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        hashlib.scrypt(b"x", salt=b"0" * 16, n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024)  # gasta o mesmo tempo: nao revela se o e-mail existe
        return False


def weak_password(pw: str) -> str | None:
    if len(pw or "") < 8:
        return "a senha precisa de pelo menos 8 caracteres"
    return None


def norm_email(e: str) -> str:
    return str(e or "").strip().lower()


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- planos

def ensure_plans(ctx: Ctx) -> None:
    """Cria os planos padrao que ainda nao existem (os que o admin ja editou nao sao tocados)."""
    have = {r["id"] for r in ctx.store.query("SELECT id FROM plans")}
    if "pro" in have and "light" not in have:  # o plano Pro virou Light (R$ 120): mesmo id de contas e assinaturas, so o nome e o preco mudam
        with ctx.store.tx() as c:
            c.execute("UPDATE plans SET id='light', name='Light', price_cents=11900 WHERE id='pro'")
            c.execute("UPDATE subscriptions SET plan='light' WHERE plan='pro'")
        have = {r["id"] for r in ctx.store.query("SELECT id FROM plans")}
    falta = [p for p in DEFAULT_PLANS if p[0] not in have]
    with ctx.store.tx() as c:
        for p in falta:
            c.execute("INSERT INTO plans(id,name,session_limit_tokens,week_limit_tokens,image_quota_month,safety_level,max_devices,price_cents,video_quota_month,audio_quota_month) VALUES(?,?,?,?,?,?,?,?,?,?)", p)
        for p in DEFAULT_PLANS:  # plano que ja existia antes da cota de midia: preenche so o que ainda e NULL (o que o admin editou nao e tocado)
            c.execute("UPDATE plans SET video_quota_month=? WHERE id=? AND video_quota_month IS NULL", (p[8], p[0]))
            c.execute("UPDATE plans SET audio_quota_month=? WHERE id=? AND audio_quota_month IS NULL", (p[9], p[0]))


def get_plan(ctx: Ctx, plan_id: str | None):
    ensure_plans(ctx)
    return ctx.store.one("SELECT * FROM plans WHERE id=?", (plan_id or "",))


# ---------------------------------------------------------------- contas

def find_account(ctx: Ctx, email: str):
    return ctx.store.one("SELECT * FROM accounts WHERE email=?", (norm_email(email),))


def new_setup_token(ctx: Ctx, account_id: int) -> str:
    """Link de definir/trocar senha: valido por 7 dias, de uso unico (so o hash fica no banco)."""
    tok = secrets.token_urlsafe(32)
    with ctx.store.tx() as c:
        c.execute("UPDATE accounts SET setup_hash=?, setup_expires=? WHERE id=?", (sha256(tok), ctx.clock() + SETUP_TTL_S, account_id))
    return tok


def create_account(ctx: Ctx, email: str, name: str, plan_id: str | None, safety_level: int | None = None) -> tuple[dict, str]:
    """Cria conta + pessoa (DESABILITADA, aguardando o administrador). Devolve (conta, token para definir a senha)."""
    ensure_plans(ctx)
    plan = get_plan(ctx, plan_id) or get_plan(ctx, "light")
    email = norm_email(email)
    now = ctx.clock()
    level = safety_level if safety_level in (1, 2) else int(plan["safety_level"] or 1)
    with ctx.store.tx() as c:
        pid = c.execute("INSERT INTO people(name,note,status,created_at,safety_level) VALUES(?,?,?,?,?)",
                        ((name or email)[:80], f"[conta] {email}", "suspended", now, level)).lastrowid
        c.execute("INSERT INTO limits(person_id,session_limit_tokens,week_limit_tokens,session_seconds,week_seconds) VALUES(?,?,?,?,?)",
                  (pid, plan["session_limit_tokens"], plan["week_limit_tokens"], ctx.s.default_session_seconds, ctx.s.default_week_seconds))
        aid = c.execute("INSERT INTO accounts(email,name,status,person_id,created_at) VALUES(?,?,?,?,?)", (email, (name or "")[:80], "active", pid, now)).lastrowid
    return dict(ctx.store.one("SELECT * FROM accounts WHERE id=?", (aid,))), new_setup_token(ctx, aid)


def apply_plan_limits(ctx: Ctx, person_id: int, plan) -> None:
    with ctx.store.tx() as c:
        c.execute("UPDATE limits SET session_limit_tokens=?, week_limit_tokens=? WHERE person_id=?", (plan["session_limit_tokens"], plan["week_limit_tokens"], person_id))


def set_person_status(ctx: Ctx, person_id: int, status: str) -> None:
    with ctx.store.tx() as c:
        c.execute("UPDATE people SET status=? WHERE id=?", (status, person_id))


def grace_days(ctx: Ctx) -> int:
    v = ctx.store.get_setting("billing_grace_days")
    return int(v) if v and v.isdigit() else GRACE_DAYS_DEFAULT


def auto_enable(ctx: Ctx) -> bool:
    return ctx.store.get_setting("billing_auto_enable") == "1"


def current_sub(ctx: Ctx, account_id: int):
    return ctx.store.one("SELECT * FROM subscriptions WHERE account_id=? ORDER BY updated_at DESC, id DESC LIMIT 1", (account_id,))


def sub_allows_use(ctx: Ctx, sub) -> bool:
    """Assinatura vale se esta ativa, ou atrasada dentro da carencia."""
    if not sub:
        return False
    if sub["status"] == "active":
        return True
    if sub["status"] == "late":
        ref = sub["next_payment_at"] or sub["updated_at"]
        return ctx.clock() <= ref + grace_days(ctx) * 86400
    return False


# ---------------------------------------------------------------- webhook da Cakto
# ATENCAO: os nomes de campo e de evento abaixo sao a leitura TOLERANTE do que costuma vir num webhook de checkout. Quando a conta
# da Cakto existir, confirmar com um evento real e ajustar aqui (e em tests/test_accounts.py). Nada e inventado como definitivo.

EVENT_STATUS = [  # (trecho do nome do evento, status). A primeira que casar vale.
    ("chargeback", "chargeback"), ("refund", "refunded"), ("reembols", "refunded"), ("cancel", "canceled"),
    ("late", "late"), ("overdue", "late"), ("atras", "late"), ("refus", "late"), ("declin", "late"), ("fail", "late"),
    ("approved", "active"), ("paid", "active"), ("renew", "active"), ("aprovad", "active"), ("pago", "active"),
    ("pending", "pending"), ("waiting", "pending"), ("created", "pending"), ("aguard", "pending"),
]


def _dig(d, *paths):
    for path in paths:
        cur = d
        for k in path.split("."):
            cur = cur.get(k) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, ""):
            return cur
    return None


def _ts(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v / 1000 if v > 1e12 else v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None).replace(tzinfo=timezone.utc).timestamp() if "T" in str(v) or "-" in str(v) else None
    except ValueError:
        return None


def parse_cakto(payload: dict) -> dict:
    event = str(_dig(payload, "event", "type", "event_type", "status") or "").lower()
    status = next((st for frag, st in EVENT_STATUS if frag in event), "unknown")
    amount = _dig(payload, "data.amount", "data.value", "data.price", "amount", "value")
    try:
        cents = int(round(float(amount) * 100)) if amount is not None else None
    except (TypeError, ValueError):
        cents = None
    return {
        "event": event, "status": status,
        "email": norm_email(_dig(payload, "data.customer.email", "customer.email", "data.email", "email") or ""),
        "name": str(_dig(payload, "data.customer.name", "customer.name", "data.name", "name") or "")[:80],
        "plan_hint": str(_dig(payload, "data.product.name", "data.offer.name", "data.plan", "product.name", "offer.name", "plan") or "").lower(),
        "external_id": str(_dig(payload, "data.subscription.id", "subscription.id", "data.subscription_id", "data.id", "data.order.id", "id") or ""),
        "event_id": str(_dig(payload, "event_id", "data.event_id", "data.transaction_id", "data.id", "id") or ""),
        "next_payment_at": _ts(_dig(payload, "data.next_charge_date", "data.next_payment_date", "data.subscription.next_payment_date", "next_payment_date")),
        "amount_cents": cents,
    }


def plan_from_hint(ctx: Ctx, hint: str) -> str | None:
    raw = ctx.store.get_setting("billing_plan_map")
    mapping = {}
    if raw:
        try:
            mapping = json.loads(raw)
        except ValueError:
            pass
    for frag, pid in mapping.items():
        if frag.lower() in hint:
            return pid
    for pid in ("enterprise", "infinity", "ultra", "max", "light", "lite", "pro"):  # do maior para o menor: "Ultra Max" nao vira Lite
        if pid in hint:
            return "light" if pid in ("pro", "lite") else pid
    return None


def skill_pack(plan_id: str | None) -> bool:
    """O plano inclui o pacote de skills profissionais? (Light nao; o app so instala o pacote para quem tem.)"""
    return (plan_id or "") not in PLANS_WITHOUT_SKILL_PACK


def process_billing(ctx: Ctx, provider: str, body_text: str, payload: dict) -> dict:
    """Aplica um evento de pagamento. Idempotente: o mesmo evento duas vezes nao duplica nada."""
    ev = parse_cakto(payload) if provider == "cakto" else parse_cakto(payload)
    event_id = ev["event_id"] or hashlib.sha256(body_text.encode()).hexdigest()
    now = ctx.clock()
    try:
        with ctx.store.tx() as c:
            c.execute("INSERT INTO billing_events(provider,event_id,kind,received_at,processed,payload) VALUES(?,?,?,?,0,?)", (provider, f"{event_id}:{ev['event']}", ev["event"][:60], now, body_text[:20000]))
    except Exception:  # noqa: BLE001  chave unica: ja recebido
        return {"ok": True, "duplicate": True}
    error = None
    try:
        _apply(ctx, ev, now)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"[:300]
    with ctx.store.tx() as c:
        c.execute("UPDATE billing_events SET processed=?, error=? WHERE provider=? AND event_id=?", (0 if error else 1, error, provider, f"{event_id}:{ev['event']}"))
    if error:
        raise RuntimeError(error)
    return {"ok": True, "duplicate": False, "status": ev["status"]}


def _apply(ctx: Ctx, ev: dict, now: float) -> None:
    if ev["status"] == "unknown" or not EMAIL_RE.match(ev["email"] or ""):
        return  # evento que a gente nao usa (ou sem e-mail): fica so no registro
    plan_id = plan_from_hint(ctx, ev["plan_hint"])
    acc = find_account(ctx, ev["email"])
    if acc is None:
        acc, token = create_account(ctx, ev["email"], ev["name"], plan_id)
        _notify_new_account(ctx, acc, token)
    from app import vms  # produto "maquina virtual": cobranca propria da VM, nao mexe no plano nem no acesso da conta
    if vms.eh_produto_vm(ctx, ev["plan_hint"]):
        vms.registrar_pagamento(ctx, acc["id"], ev)
        if plan_id is None:
            return  # so a VM
        # produto combinado ("<plano> + maquina virtual"): alem do pedido da VM, aplica o plano abaixo
    plan = get_plan(ctx, plan_id) if plan_id else None
    sub = ctx.store.one("SELECT * FROM subscriptions WHERE account_id=? AND (external_id=? OR ?='') ORDER BY id DESC LIMIT 1", (acc["id"], ev["external_id"], ev["external_id"]))
    with ctx.store.tx() as c:
        if sub is None:
            c.execute("INSERT INTO subscriptions(account_id,plan,status,provider,external_id,next_payment_at,amount_cents,started_at,updated_at,blocked_by_billing) VALUES(?,?,?,?,?,?,?,?,?,0)",
                      (acc["id"], plan_id or "light", ev["status"], "cakto", ev["external_id"] or None, ev["next_payment_at"], ev["amount_cents"], now, now))
        else:
            novo = sub["status"] if ev["status"] == "pending" and sub["status"] in ("active", "late") else ev["status"]  # "criada/aguardando" chega junto com a aprovacao e nao pode rebaixar quem ja esta pago
            c.execute("UPDATE subscriptions SET status=?, plan=?, next_payment_at=COALESCE(?, next_payment_at), amount_cents=COALESCE(?, amount_cents), updated_at=? WHERE id=?",
                      (novo, plan_id or sub["plan"], ev["next_payment_at"], ev["amount_cents"], now, sub["id"]))
    sub = current_sub(ctx, acc["id"])
    if plan:
        apply_plan_limits(ctx, acc["person_id"], plan)
    person = ctx.store.one("SELECT status FROM people WHERE id=?", (acc["person_id"],))
    if ev["status"] in ("canceled", "refunded", "chargeback"):
        if person["status"] == "active":
            set_person_status(ctx, acc["person_id"], "suspended")
            with ctx.store.tx() as c:
                c.execute("UPDATE subscriptions SET blocked_by_billing=1 WHERE id=?", (sub["id"],))
    elif ev["status"] == "active":
        if person["status"] == "suspended" and (sub["blocked_by_billing"] or auto_enable(ctx)):  # voltou a pagar: reabre quem o admin ja tinha liberado
            set_person_status(ctx, acc["person_id"], "active")
            with ctx.store.tx() as c:
                c.execute("UPDATE subscriptions SET blocked_by_billing=0 WHERE id=?", (sub["id"],))


def _notify_new_account(ctx: Ctx, acc: dict, token: str) -> None:
    try:
        from app import mailer
        mailer.send_welcome(ctx, acc["email"], acc.get("name") or "", token)
    except Exception:  # noqa: BLE001  e-mail nunca derruba o webhook
        pass


def lapse_sweep(ctx: Ctx) -> int:
    """Bloqueia quem passou da carencia do atraso. Roda de hora em hora e a cada chamada do painel de assinantes."""
    n = 0
    cut = ctx.clock()
    for s in ctx.store.query("SELECT s.*, a.person_id FROM subscriptions s JOIN accounts a ON a.id=s.account_id WHERE s.status='late' AND s.blocked_by_billing=0"):
        ref = s["next_payment_at"] or s["updated_at"]
        if cut > ref + grace_days(ctx) * 86400:
            set_person_status(ctx, s["person_id"], "suspended")
            with ctx.store.tx() as c:
                c.execute("UPDATE subscriptions SET blocked_by_billing=1 WHERE id=?", (s["id"],))
            n += 1
    return n


# ---------------------------------------------------------------- login do app

def device_key(ctx: Ctx, person_id: int, device: str, max_devices: int) -> str:
    """Emite uma chave nova para este computador; passando de `max_devices`, a mais antiga e revogada."""
    key = new_api_key()
    now = ctx.clock()
    label = "app:" + (device or "computador")[:40]
    with ctx.store.tx() as c:
        if max_devices and max_devices > 0:  # 0 = ilimitado: nao revoga nada
            antigas = c.execute("SELECT id FROM keys WHERE person_id=? AND revoked_at IS NULL AND label LIKE 'app:%' ORDER BY id ASC", (person_id,)).fetchall()
            for k in antigas[: max(0, len(antigas) - (max_devices - 1))]:
                c.execute("UPDATE keys SET revoked_at=? WHERE id=?", (now, k["id"]))
        c.execute("INSERT INTO keys(person_id,key_hash,key_last4,created_at,label) VALUES(?,?,?,?,?)", (person_id, sha256(key), key[-4:], now, label))
    return key
