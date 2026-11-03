"""Auto-cadastro dos aplicativos (Segundo Cerebro): POST /v1/enroll.

A instalacao pede uma chave sem senha nenhuma. A pessoa nasce SUSPENSA: nao consome nada ate o dono achar o codigo
no painel (/admin, busca por nome) e clicar em retomar. O codigo tem o formato de placa Mercosul (ABC1D23) e
vira o nome da pessoa. A chave e devolvida uma unica vez (o servidor so guarda o hash).
"""
from __future__ import annotations

import secrets
from collections import defaultdict, deque

from fastapi import APIRouter, Request

from app.auth import new_api_key, sha256
from app.ctx import fail
from app.admin import _db, _ip, _json

router = APIRouter()

LETRAS = "ABCDEFGHJKLMNPRSTVWXYZ"  # sem I, O, Q, U: nao confundem com 1, 0
POR_HORA_POR_IP = 5
MAX_PENDENTES = 300


def novo_codigo() -> str:
    l = lambda: secrets.choice(LETRAS)  # noqa: E731
    d = lambda: secrets.choice("0123456789")  # noqa: E731
    return f"{l()}{l()}{l()}{d()}{l()}{d()}{d()}"


def _limpa(v, n: int) -> str:
    return "".join(ch for ch in str(v or "") if ch.isprintable())[:n].strip()


@router.post("/v1/enroll")
@_db
async def enroll(request: Request):
    ctx = request.app.state.ctx
    ip, agora = _ip(request), ctx.clock()
    tentativas = getattr(request.app.state, 'enroll_tentativas', None)
    if tentativas is None:
        tentativas = request.app.state.enroll_tentativas = defaultdict(deque)
    fila = tentativas[ip]
    while fila and agora - fila[0] > 3600:
        fila.popleft()
    if len(fila) >= POR_HORA_POR_IP:
        raise fail(429, "muitos cadastros deste endereco; tente mais tarde", "rate_limit_error", "enroll_rate_limited", headers={"Retry-After": "3600"})
    fila.append(agora)

    b = await _json(request)
    maquina = _limpa(b.get("machine_id"), 64)
    if len(maquina) < 8:
        raise fail(422, "machine_id obrigatorio", "invalid_request_error", "invalid_machine")
    app_nome = _limpa(b.get("app"), 40) or "app"

    ja = ctx.store.query("SELECT id,name,status FROM people WHERE note LIKE ? ORDER BY id DESC LIMIT 1", (f"%[maquina:{maquina}]%",))
    if ja:  # mesma instalacao pedindo de novo: nao cria outra pessoa, nao reemite a chave (o app ja a guardou)
        return {"code": ja[0]["name"], "status": ja[0]["status"], "api_key": None}

    pendentes = ctx.store.one("SELECT COUNT(*) AS n FROM people WHERE status='suspended' AND note LIKE '[auto]%'")
    if pendentes and pendentes["n"] >= MAX_PENDENTES:
        raise fail(503, "fila de liberacao cheia; avise o suporte", "service_unavailable", "enroll_full")

    host = _limpa(b.get("host"), 60)
    so = _limpa(b.get("os"), 20)
    versao = _limpa(b.get("version"), 20)
    key = new_api_key()
    with ctx.store.tx() as c:
        for _ in range(20):
            codigo = novo_codigo()
            if not c.execute("SELECT 1 FROM people WHERE name=?", (codigo,)).fetchone():
                break
        else:
            raise fail(503, "nao foi possivel gerar o codigo", "service_unavailable", "enroll_code")
        nota = f"[auto] {app_nome} {versao} · {host} · {so} · ip {ip} [maquina:{maquina}]"
        pid = c.execute("INSERT INTO people(name,note,status,created_at) VALUES(?,?,?,?)", (codigo, nota, "suspended", agora)).lastrowid
        c.execute("INSERT INTO limits(person_id,session_limit_tokens,week_limit_tokens,session_seconds,week_seconds) VALUES(?,?,?,?,?)",
                  (pid, None, None, ctx.s.default_session_seconds, ctx.s.default_week_seconds))
        c.execute("INSERT INTO keys(person_id,key_hash,key_last4,created_at) VALUES(?,?,?,?)", (pid, sha256(key), key[-4:], agora))
    return {"code": codigo, "status": "suspended", "api_key": key}
