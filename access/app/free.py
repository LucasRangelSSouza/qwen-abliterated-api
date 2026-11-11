"""Teste gratis (funil de venda): cadastro com e-mail + codigo, 5 perguntas por pessoa numa IA sem censura, sem Hermes, sem chave de API.
Rotas publicas /v1/free/*. A pessoa nao recebe chave: so um token assinado de sessao, que vale apenas para /v1/free/chat e /v1/free/me.
Quando as 5 perguntas acabam, o servidor responde 402 `free_limit` e a tela mostra os planos. Comprando com o MESMO e-mail, o webhook da Cakto
acha esta conta (find_account) e a promove: nao existe segunda conta."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import accounts as A
from app import mailer
from app.admin import _db, _ip, _json
from app.ctx import err, fail

router = APIRouter()

LIMITE_PADRAO = 5
TOKEN_TTL_S = 7 * 86400
CODIGO_TTL_S = 15 * 60
CODIGO_TENTATIVAS = 5
MAX_MSGS = 12          # historico enviado ao modelo: so o final da conversa
MAX_CHARS_MSG = 4000
MAX_CHARS_TOTAL = 14000
MAX_TOKENS_RESP = 900
MODELO_GRATIS = "qwen-abliterated"
SISTEMA = ("Você é o Segundo Cérebro, a IA sem censura da Rangel Tech, em uma conversa de teste grátis. Responda em português do Brasil, direto e útil, "
           "sem sermão e sem avisos desnecessários. Nesta versão de teste você só conversa por texto: não cria arquivos, imagens, vídeos nem acessa a internet. "
           "Se a pessoa pedir isso, diga em uma frase que está nos planos completos e responda o que puder com texto.")

# domínios de e-mail descartável mais comuns (o filtro principal é o código por e-mail; isto só tira o abuso óbvio)
DESCARTAVEIS = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com",
                "getnada.com", "maildrop.cc", "dispostable.com", "throwawaymail.com", "fakeinbox.com", "mintemail.com", "emailondeck.com", "tempail.com"}


def limite(ctx) -> int:
    v = ctx.store.get_setting("free_questions")
    return int(v) if v and v.isdigit() and int(v) > 0 else LIMITE_PADRAO


def identidade(email: str) -> str:
    """Mesma pessoa, mesmo e-mail: tira +etiqueta de qualquer provedor e os pontos do Gmail, para não multiplicar o teste."""
    e = A.norm_email(email)
    local, _, dom = e.partition("@")
    local = local.split("+", 1)[0]
    if dom in ("gmail.com", "googlemail.com"):
        local, dom = local.replace(".", ""), "gmail.com"
    return f"{local}@{dom}"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _segredo(ctx) -> bytes:
    return ctx.admin.secret + b"|free"


def emitir_token(ctx, account_id: int) -> str:
    corpo = _b64(json.dumps({"a": account_id, "e": int(ctx.clock() + TOKEN_TTL_S)}, separators=(",", ":")).encode())
    return corpo + "." + hmac.new(_segredo(ctx), corpo.encode(), hashlib.sha256).hexdigest()


def conta_do_token(ctx, request: Request) -> int:
    h = request.headers.get("authorization", "")
    tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
    corpo, _, sig = tok.partition(".")
    if not corpo or not hmac.compare_digest(sig, hmac.new(_segredo(ctx), corpo.encode(), hashlib.sha256).hexdigest()):
        raise fail(401, "Sessão inválida. Entre de novo.", "invalid_request_error", "free_auth")
    try:
        d = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))
        if float(d["e"]) < ctx.clock():
            raise ValueError
        return int(d["a"])
    except Exception:  # noqa: BLE001
        raise fail(401, "Sessão expirada. Entre de novo.", "invalid_request_error", "free_auth")


def _acelerador(request: Request, chave: str, max_n: int, janela_s: float, agora: float) -> bool:
    t = getattr(request.app.state, "free_throttle", None)
    if t is None:
        t = request.app.state.free_throttle = defaultdict(deque)
    q = t[chave]
    while q and agora - q[0] > janela_s:
        q.popleft()
    if len(q) >= max_n:
        return False
    q.append(agora)
    return True


def _hash_codigo(codigo: str, account_id: int) -> str:
    return hashlib.sha256(f"{account_id}:{codigo}".encode()).hexdigest()


def _enviar_codigo(ctx, email: str, account_id: int) -> str:
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    with ctx.store.tx() as c:
        c.execute("UPDATE free_trials SET code_hash=?, code_expires=?, code_tries=0 WHERE account_id=?",
                  (_hash_codigo(codigo, account_id), ctx.clock() + CODIGO_TTL_S, account_id))
    texto = f"Seu código para testar o Segundo Cérebro é {codigo}. Ele vale por 15 minutos.\n\nSe não foi você, ignore este e-mail."
    corpo = mailer.render_html("Seu código de verificação", ["Use este código para liberar o seu teste grátis do Segundo Cérebro:", codigo],
                               "Abrir o teste", f"{mailer.site_url()}/testar", "O código vale por 15 minutos. Se não foi você, ignore este e-mail.")
    mailer.send(email, f"Seu código: {codigo}", texto, corpo)
    return codigo


def _trial(ctx, account_id: int):
    return ctx.store.one("SELECT * FROM free_trials WHERE account_id=?", (account_id,))


def _estado(ctx, account_id: int) -> dict:
    t = _trial(ctx, account_id)
    lim = limite(ctx)
    usadas = int(t["used"]) if t else 0
    return {"used": usadas, "limit": lim, "remaining": max(0, lim - usadas)}


def _pago(ctx, account_id: int) -> bool:
    sub = A.current_sub(ctx, account_id)
    return bool(sub and A.sub_allows_use(ctx, sub))


@router.post("/v1/free/signup")
@_db
async def signup(request: Request):
    ctx = request.app.state.ctx
    agora = ctx.clock()
    b = await _json(request)
    if str(b.get("website") or ""):  # campo-isca: gente de verdade não preenche
        return {"ok": True, "need_code": True}
    email = A.norm_email(b.get("email"))
    senha = str(b.get("password") or "")
    nome = str(b.get("name") or "").strip()[:80]
    if not A.EMAIL_RE.match(email) or len(email) > 200:
        raise fail(400, "Informe um e-mail válido.", "invalid_request_error", "invalid_email")
    if (msg := A.weak_password(senha)):
        raise fail(400, msg[0].upper() + msg[1:] + ".", "invalid_request_error", "weak_password")
    if email.split("@")[1] in DESCARTAVEIS:
        raise fail(400, "Use um e-mail que você acessa de verdade (e-mails temporários não funcionam).", "invalid_request_error", "disposable_email")
    if not _acelerador(request, "signup:" + _ip(request), 4, 86400, agora):
        raise fail(429, "Muitos cadastros deste endereço hoje. Tente amanhã ou escolha um plano.", "rate_limit_error", "signup_limit")
    ident = identidade(email)
    if A.find_account(ctx, email) is not None or ctx.store.one("SELECT 1 FROM free_trials WHERE ident=?", (ident,)):
        raise fail(409, "Este e-mail já tem cadastro. Entre com a sua senha.", "invalid_request_error", "exists")
    from app import terms
    if not b.get("aceite_termos"):  # sem a caixa marcada nada e criado (Spec 25 §18.1)
        raise fail(400, "Para continuar, leia e aceite os Termos de uso.", "invalid_request_error", "terms_required")
    acc, _tok = A.create_account(ctx, email, nome, "light", safety_level=1)
    with ctx.store.tx() as c:
        c.execute("UPDATE accounts SET pw_hash=?, setup_hash=NULL, setup_expires=NULL WHERE id=?", (A.hash_password(senha), acc["id"]))
        c.execute("INSERT INTO free_trials(account_id,ident,used,verified,created_at,ip) VALUES(?,?,0,0,?,?)", (acc["id"], ident, agora, _ip(request)))
    terms.exigir_aceite(ctx, b, request, email, "site", account_id=acc["id"])
    _enviar_codigo(ctx, email, acc["id"])
    return {"ok": True, "need_code": True}


@router.post("/v1/free/resend")
@_db
async def resend(request: Request):
    ctx = request.app.state.ctx
    b = await _json(request)
    email = A.norm_email(b.get("email"))
    acc = A.find_account(ctx, email)
    t = _trial(ctx, acc["id"]) if acc else None
    if t and not t["verified"] and _acelerador(request, "resend:" + email, 3, 3600, ctx.clock()):
        _enviar_codigo(ctx, email, acc["id"])
    return {"ok": True}  # sempre igual: não revela quem tem cadastro


@router.post("/v1/free/verify")
@_db
async def verify(request: Request):
    ctx = request.app.state.ctx
    b = await _json(request)
    email, codigo = A.norm_email(b.get("email")), re.sub(r"\D", "", str(b.get("code") or ""))
    acc = A.find_account(ctx, email)
    t = _trial(ctx, acc["id"]) if acc else None
    if not t or not t["code_hash"]:
        raise fail(400, "Código inválido ou expirado. Peça um novo.", "invalid_request_error", "bad_code")
    if int(t["code_tries"]) >= CODIGO_TENTATIVAS or float(t["code_expires"] or 0) < ctx.clock():
        raise fail(400, "Código inválido ou expirado. Peça um novo.", "invalid_request_error", "bad_code")
    if not hmac.compare_digest(_hash_codigo(codigo, acc["id"]), t["code_hash"]):
        with ctx.store.tx() as c:
            c.execute("UPDATE free_trials SET code_tries=code_tries+1 WHERE account_id=?", (acc["id"],))
        raise fail(400, "Código incorreto.", "invalid_request_error", "bad_code")
    with ctx.store.tx() as c:
        c.execute("UPDATE free_trials SET verified=1, code_hash=NULL WHERE account_id=?", (acc["id"],))
    return {"token": emitir_token(ctx, acc["id"]), "email": email, **_estado(ctx, acc["id"])}


@router.post("/v1/free/login")
@_db
async def login(request: Request):
    ctx = request.app.state.ctx
    agora = ctx.clock()
    b = await _json(request)
    email, senha = A.norm_email(b.get("email")), str(b.get("password") or "")
    if not _acelerador(request, "login:" + _ip(request), 20, 600, agora):
        raise fail(429, "Muitas tentativas. Aguarde alguns minutos.", "rate_limit_error", "login_limit")
    acc = A.find_account(ctx, email)
    if not acc or acc["status"] != "active" or not A.verify_password(senha, acc["pw_hash"]):
        raise fail(401, "E-mail ou senha incorretos.", "invalid_request_error", "bad_login")
    if _pago(ctx, acc["id"]):  # já é assinante: o teste não se aplica
        return {"paid": True, "email": email, "message": "Você já é assinante. Entre pelo aplicativo ou em remoto.rangeltech.net."}
    t = _trial(ctx, acc["id"])
    if not t:
        raise fail(403, "Esta conta não tem teste grátis. Entre pelo aplicativo.", "invalid_request_error", "no_trial")
    if not t["verified"]:
        _enviar_codigo(ctx, email, acc["id"])
        return JSONResponse(err("Falta confirmar o e-mail: enviamos um código.", "invalid_request_error", "need_code"), status_code=403)
    return {"token": emitir_token(ctx, acc["id"]), "email": email, **_estado(ctx, acc["id"])}


@router.get("/v1/free/me")
@_db
async def me(request: Request):
    ctx = request.app.state.ctx
    aid = conta_do_token(ctx, request)
    acc = ctx.store.one("SELECT email FROM accounts WHERE id=?", (aid,))
    return {"email": acc["email"] if acc else "", "paid": _pago(ctx, aid), **_estado(ctx, aid)}


def _mensagens(b: dict) -> list[dict]:
    brutas = b.get("messages")
    if not isinstance(brutas, list) or not brutas:
        raise fail(400, "Envie a sua pergunta.", "invalid_request_error", "invalid_messages")
    out = []
    for m in brutas[-MAX_MSGS:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            continue
        out.append({"role": m["role"], "content": m["content"][:MAX_CHARS_MSG]})
    while out and sum(len(m["content"]) for m in out) > MAX_CHARS_TOTAL:
        out.pop(0)
    if not out or out[-1]["role"] != "user" or not out[-1]["content"].strip():
        raise fail(400, "Envie a sua pergunta.", "invalid_request_error", "invalid_messages")
    return out


def _sse(obj) -> bytes:
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"


@router.post("/v1/free/chat")
@_db
async def chat(request: Request):
    ctx = request.app.state.ctx
    aid = conta_do_token(ctx, request)
    b = await _json(request)
    msgs = _mensagens(b)
    t = _trial(ctx, aid)
    if not t or not t["verified"]:
        raise fail(403, "Confirme o seu e-mail para usar o teste.", "invalid_request_error", "need_code")
    if _pago(ctx, aid):
        raise fail(403, "Você já é assinante: use o aplicativo.", "invalid_request_error", "paid")
    if not _acelerador(request, f"chat:{aid}", 8, 60, ctx.clock()) or not _acelerador(request, "chatip:" + _ip(request), 30, 60, ctx.clock()):
        raise fail(429, "Calma, uma pergunta por vez.", "rate_limit_error", "slow_down")
    lim = limite(ctx)
    with ctx.store.tx() as c:  # reserva a pergunta ANTES de chamar o modelo (atômico: duas abas não passam do limite)
        cur = c.execute("UPDATE free_trials SET used=used+1, last_at=? WHERE account_id=? AND used<?", (ctx.clock(), aid, lim))
        reservou = (cur.rowcount or 0) > 0
    if not reservou:
        return JSONResponse(err("Suas perguntas grátis acabaram. Escolha um plano para continuar.", "payment_required", "free_limit", **_estado(ctx, aid)), status_code=402)

    def devolver():
        with ctx.store.tx() as c:
            c.execute("UPDATE free_trials SET used=used-1 WHERE account_id=? AND used>0", (aid,))

    corpo = {"model": MODELO_GRATIS, "stream": True, "stream_options": {"include_usage": True}, "max_tokens": MAX_TOKENS_RESP,
             "temperature": 0.7, "top_p": 0.8, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "system", "content": SISTEMA}, *msgs]}
    headers = {"Content-Type": "application/json", "x-end-user": f"gratis-{aid}", "x-queue-class": "interactive"}
    if ctx.s.upstream_key:
        headers["Authorization"] = f"Bearer {ctx.s.upstream_key}"
    try:
        req = ctx.http.build_request("POST", ctx.s.upstream_base + "/v1/chat/completions", json=corpo, headers=headers)
        up = await ctx.http.send(req, stream=True)
    except httpx.HTTPError:
        devolver()
        raise fail(502, "A IA está ocupada agora. Tente de novo em instantes (sua pergunta não foi descontada).", "server_error", "upstream_error")
    if up.status_code != 200:
        try:
            await up.aread()
        finally:
            await up.aclose()
        devolver()
        raise fail(502, "A IA está ocupada agora. Tente de novo em instantes (sua pergunta não foi descontada).", "server_error", "upstream_error")
    estado = _estado(ctx, aid)

    async def gen():
        ok = False
        try:
            async for pedaco in up.aiter_raw():
                if b"[DONE]" in pedaco:
                    ok = True
                    pedaco = pedaco.replace(b"data: [DONE]" + b"\n\n", b"")  # o fim é enviado por nós, depois do aviso de saldo
                if pedaco:
                    yield pedaco
        except httpx.HTTPError:
            yield _sse(err("A conexão com a IA foi interrompida.", "server_error", "upstream_error"))
        finally:
            await up.aclose()
            if not ok:
                devolver()  # não terminou: a pergunta não conta
        if ok:
            yield _sse({"free": {**estado, "limit_reached": estado["remaining"] == 0}})
            yield b"data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
