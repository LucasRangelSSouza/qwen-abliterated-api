"""Relatorio de falha de instalacao do app (Segundo Cerebro).
O instalador roda antes de existir conta, entao o endpoint e anonimo: aceita so um texto curto com limite por endereco, guarda os ultimos
relatorios na tabela `settings` (chave `diag:<ts>:<id>`) e o painel/admin le em GET /admin/api/diag. Nao guarda nada alem do que o app manda
(versao, sistema, etapa, ultimas linhas do preparo.log, ja sem o nome de usuario do Windows)."""
from __future__ import annotations

import json
import secrets
from collections import defaultdict, deque

from fastapi import APIRouter, Request

from app.admin import _db, _ip, _json, api, require
from app.ctx import fail

router = APIRouter()

MAX_LOG = 8000        # caracteres do log
MAX_CAMPO = 80
POR_HORA = 20         # relatorios por endereco por hora
GUARDAR = 300         # relatorios mantidos (os mais antigos saem)
_PREFIXO = "diag:"


def _campo(v, n=MAX_CAMPO) -> str:
    return str(v if v is not None else "")[:n]


@router.post("/v1/diag/instalacao")
@_db
async def receber(request: Request):
    ctx = request.app.state.ctx
    b = await _json(request)
    agora = ctx.clock()
    mapa = getattr(request.app.state, "diag_hits", None)
    if mapa is None:
        mapa = request.app.state.diag_hits = defaultdict(deque)
    hits = mapa[_ip(request)]
    while hits and agora - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= POR_HORA:
        raise fail(429, "muitos relatorios", "rate_limit_error", "diag_limit")
    hits.append(agora)
    rel = {"ts": int(agora), "ip": _ip(request), "versao": _campo(b.get("versao"), 20), "so": _campo(b.get("so")), "arch": _campo(b.get("arch"), 20),
           "etapa": _campo(b.get("etapa")), "codigo": _campo(b.get("codigo"), 20), "maquina": _campo(b.get("maquina"), 40), "log": _campo(b.get("log"), MAX_LOG)}
    ctx.store.set_setting(f"{_PREFIXO}{int(agora)}:{secrets.token_hex(3)}", json.dumps(rel, ensure_ascii=False))
    velhos = ctx.store.query("SELECT key FROM settings WHERE key LIKE ? ORDER BY key DESC", (_PREFIXO + "%",))[GUARDAR:]
    if velhos:
        with ctx.store.tx() as c:
            for r in velhos:
                c.execute("DELETE FROM settings WHERE key=?", (r["key"],))
    return {"ok": True}


@api.get("/diag")
@_db
def listar(request: Request, limit: int = 50):
    ctx = require(request)
    rows = ctx.store.query("SELECT key,value FROM settings WHERE key LIKE ? ORDER BY key DESC", (_PREFIXO + "%",))[: max(1, min(limit, 300))]
    out = []
    for r in rows:
        try:
            out.append({"id": r["key"], **json.loads(r["value"])})
        except ValueError:
            continue
    return {"relatorios": out}
