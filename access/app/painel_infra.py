"""Tela "Infraestrutura" do painel (spec 57 §6.4): contêineres da VPS principal pelo proxy do Docker SÓ LEITURA
(rt-docker-leitura), com o produto de cada um (registro único) e a saúde do Uptime Kuma (página de status pública)."""
from __future__ import annotations

import os
import re
import time

from fastapi import Request

from app.admin import api, require
from app.painel_produtos import registro

PROXY = os.environ.get("DOCKER_LEITURA_URL", "http://rt-docker-leitura:2375")
KUMA = "https://uptime.rangeltech.net/api/status-page"
_cache: dict = {}


async def _kuma(ctx) -> dict:
    if "kuma" in _cache and time.time() - _cache["kuma"][0] < 60:
        return _cache["kuma"][1]
    out = {}
    try:
        sp = (await ctx.http.get(f"{KUMA}/rangeltech", timeout=15)).json()
        hb = (await ctx.http.get(f"{KUMA}/heartbeat/rangeltech", timeout=15)).json()["heartbeatList"]
        for g in sp["publicGroupList"]:
            for m in g["monitorList"]:
                b = hb.get(str(m["id"])) or []
                out[m["name"]] = {0: "fora", 1: "ok", 2: "pendente", 3: "manutenção"}.get(b[-1]["status"], "?") if b else "sem dados"
    except Exception:  # noqa: BLE001
        pass
    _cache["kuma"] = (time.time(), out)
    return out


@api.get("/painel/infra")
async def painel_infra(request: Request):
    ctx = require(request)
    try:
        cs = (await ctx.http.get(f"{PROXY}/containers/json?all=1", timeout=10)).json()
    except Exception as e:  # noqa: BLE001
        return {"erro": f"não consegui ler os contêineres: {type(e).__name__}"}
    kuma = await _kuma(ctx)
    reg = registro()
    por_servico = {}
    for p in reg:
        for chave in filter(None, (p.get("servico"), p.get("id"), p.get("schema"))):
            por_servico.setdefault(chave, p)
    linhas = []
    for c in cs:
        nome = (c.get("Names") or ["?"])[0].lstrip("/")
        lab = c.get("Labels") or {}
        proj = lab.get("com.docker.compose.project", "")
        p = por_servico.get(nome) or por_servico.get(lab.get("rt.service", "")) or por_servico.get(proj)
        status = c.get("Status", "")
        saude = "saudável" if "(healthy)" in status else "com problema" if "(unhealthy)" in status else "iniciando" if "health: starting" in status else "sem checagem"
        grupo = ("Produto" if p and p["tipo"] in ("produto", "variante") else "Plataforma" if p else
                 "LUAR" if re.search(r"chatwoot|luar|kernel", nome) else "Infraestrutura")
        linhas.append({"nome": nome, "imagem": (c.get("Image") or "")[:60], "estado": c.get("State"), "status": status, "saude": saude,
                       "projeto": proj, "grupo": grupo, "produto": p["nome"] if p else None,
                       "monitor": kuma.get(p["nome"]) if p else None, "link": (p or {}).get("links", {}).get("produto")})
    linhas.sort(key=lambda x: (x["estado"] == "running", x["saude"] != "com problema", x["grupo"], x["nome"]))
    return {"conteineres": linhas, "total": len(linhas), "rodando": sum(1 for x in linhas if x["estado"] == "running"),
            "com_problema": sum(1 for x in linhas if x["saude"] == "com problema" or x["estado"] != "running"),
            "kuma": {"ok": sum(1 for v in kuma.values() if v == "ok"), "fora": [k for k, v in kuma.items() if v == "fora"],
                     "url": "https://uptime.rangeltech.net/status/rangeltech"}}
