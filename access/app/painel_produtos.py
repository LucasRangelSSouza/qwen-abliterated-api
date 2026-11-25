"""Painel único (spec 57 §6.1, §6.2, §6.2A): abas de produto lendo o banco `produtos` direto (usuário `painel_leitura`, só
as colunas id/email/criado/plano/ativo_ate/pedido; nunca senha) e os links do registro único (registro_produtos.json,
exportado do vps_rt_infra). Produto sem banco ou fora do ar não derruba a aba: volta com `erro`."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import Request

from app.admin import api, require
from app.ctx import fail

REGISTRO = Path(__file__).with_name("registro_produtos.json")
QA = re.compile(r"^(demo@rangeltech\.net|contato@rangeltech\.net|comercial@rangeltech\.net|qa[-.].*@rangeltech\.net|rtqa.*|.*@fldubai\.com|.*@example\.(com|invalid))$", re.I)
_cache: dict = {}


def registro() -> list[dict]:
    try:
        return json.loads(REGISTRO.read_text(encoding="utf-8"))["produtos"]
    except (OSError, ValueError, KeyError):
        return []


def _conexao():
    import psycopg
    from psycopg.rows import dict_row
    url = os.environ.get("PRODUTOS_LEITURA_URL", "")
    if not url:
        raise RuntimeError("PRODUTOS_LEITURA_URL não configurada")
    return psycopg.connect(url, row_factory=dict_row, autocommit=True, connect_timeout=5)


def _schema_valido(s: str | None) -> str | None:
    return s if s and re.fullmatch(r"[a-z][a-z0-9_]{1,40}", s) else None


def _resumo(schema: str) -> dict:
    agora = time.time()
    with _conexao() as c, c.cursor() as cur:
        cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name='assinaturas'", (schema,))
        tem_ass = cur.fetchone() is not None
        ativo = (f'greatest(coalesce(u.ativo_ate,0), coalesce((select max(a.ativo_ate) from "{schema}".assinaturas a where a.usuario=u.id),0))'
                 if tem_ass else "coalesce(u.ativo_ate,0)")
        cur.execute(f'select u.email, u.criado, u.plano, {ativo} as ativo_ate from "{schema}".usuarios u')
        linhas = [r for r in cur.fetchall() if not QA.match(r["email"] or "")]
    ativos = [r for r in linhas if (r["ativo_ate"] or 0) > agora]
    por_plano: dict = {}
    for r in ativos:
        por_plano[r["plano"] or "sem plano"] = por_plano.get(r["plano"] or "sem plano", 0) + 1
    return {"usuarios": len(linhas), "assinantes": len(ativos), "por_plano": por_plano,
            "novos_7d": sum(1 for r in linhas if (r["criado"] or 0) > agora - 7 * 86400),
            "vencidos": sum(1 for r in linhas if 0 < (r["ativo_ate"] or 0) <= agora)}


@api.get("/painel/registro")
def painel_registro(request: Request):
    require(request)
    return {"produtos": registro()}


@api.get("/painel/produtos/{pid}")
def painel_produto(pid: str, request: Request):
    require(request)
    p = next((x for x in registro() if x["id"] == pid), None)
    if not p:
        raise fail(404, "produto não está no registro", "invalid_request_error", "produto_desconhecido")
    out = {**p}
    schema = _schema_valido(p.get("schema"))
    if schema:
        ch = ("resumo", schema)
        if ch in _cache and time.time() - _cache[ch][0] < 30:
            out["resumo"] = _cache[ch][1]
        else:
            try:
                out["resumo"] = _resumo(schema)
                _cache[ch] = (time.time(), out["resumo"])
            except Exception as e:  # noqa: BLE001
                out["erro"] = f"não consegui ler o banco do produto: {type(e).__name__}"
        if p.get("tipo") == "variante":
            out["aviso"] = f"Usa a mesma base de usuários de {p.get('base')} (contas separadas por site de venda ficam para depois)."
    return out


@api.get("/painel/produtos/{pid}/usuarios")
def painel_usuarios(pid: str, request: Request, q: str = "", situacao: str = "todos"):
    require(request)
    p = next((x for x in registro() if x["id"] == pid), None)
    schema = _schema_valido((p or {}).get("schema"))
    if not schema:
        raise fail(404, "produto sem banco no registro", "invalid_request_error", "produto_sem_banco")
    agora = time.time()
    with _conexao() as c, c.cursor() as cur:
        cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name='assinaturas'", (schema,))
        tem_ass = cur.fetchone() is not None
        ativo = (f'greatest(coalesce(u.ativo_ate,0), coalesce((select max(a.ativo_ate) from "{schema}".assinaturas a where a.usuario=u.id),0))'
                 if tem_ass else "coalesce(u.ativo_ate,0)")
        cur.execute(f'select u.id, u.email, u.criado, u.plano, {ativo} as ativo_ate from "{schema}".usuarios u '
                    "where (%s = '' or u.email ilike %s) order by u.criado desc limit 300", (q, f"%{q}%"))
        rows = cur.fetchall()
    out = []
    for r in rows:
        qa = bool(QA.match(r["email"] or ""))
        ativo_ = (r["ativo_ate"] or 0) > agora
        if situacao == "ativos" and not ativo_:
            continue
        if situacao == "inativos" and ativo_:
            continue
        out.append({"id": r["id"], "email": r["email"], "criado": r["criado"], "plano": r["plano"], "ativo_ate": r["ativo_ate"],
                    "assinante": ativo_, "qa": qa})
    return {"id": pid, "usuarios": out}
