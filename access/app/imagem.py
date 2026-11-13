"""Imagem pelo ChatGPT no 9Router global (spec 36 / decisao do Lucas 13/11/2026): a maquina de imagem (GPU de midia) saiu.

Mesmo contrato que o app ja usa (mcp-midia.js), para nada mudar no cliente:
  POST /v1/images/generations {prompt, size, style?, async?}  -> 202 {id, status:"queued"} (async) ou 200 {id, data:[{b64_json}]} (sincrono)
  GET  /v1/media/jobs/{id}                                     -> {status, progress, eta_s, message, mime, seconds, size}
  GET  /v1/jobs/{id}/file  (e /image)                          -> bytes da imagem
O llm-access chama `IMAGE_ROUTER_URL/v1/images/generations` com a chave propria (IMAGE_ROUTER_KEY, tenant platform) e o modelo
IMAGE_ROUTER_MODEL (padrao cx/gpt-image-2: assinatura ChatGPT, custo zero por imagem). A cota de imagens do plano e reservada antes e
devolvida se falhar. Resultado guardado em memoria por 1 h (o app baixa na hora)."""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid

import httpx

from app import mediaquota

log = logging.getLogger("llm-access.imagem")

TTL_S = 3600
ETA_S = 25            # medido em 13/11/2026: 20 s por imagem 1024x1024
TIMEOUT_S = 300
TAMANHOS = {"1024x1024", "1536x1024", "1024x1536", "auto"}
ESTILO = {"foto": "photorealistic photo", "realista": "photorealistic photo", "ilustracao": "digital illustration", "anime": "anime style",
          "3d": "3D render", "aquarela": "watercolor painting", "minimalista": "minimalist flat design"}

_jobs: dict[str, dict] = {}


def ligado(s) -> bool:
    return bool(s.image_router_url and s.image_router_key)


def _limpar(now: float) -> None:
    for k in [k for k, v in _jobs.items() if now - v["criado"] > TTL_S]:
        _jobs.pop(k, None)


def _tamanho(v) -> str:
    v = str(v or "1024x1024").lower().replace(" ", "")
    if v in TAMANHOS:
        return v
    try:
        w, h = (int(x) for x in v.split("x"))
    except ValueError:
        return "1024x1024"
    return "1536x1024" if w > h * 1.15 else "1024x1536" if h > w * 1.15 else "1024x1024"


def _prompt(body: dict) -> str:
    p = str(body.get("prompt") or "").strip()[:3800]
    est = ESTILO.get(str(body.get("style") or "").lower())
    neg = str(body.get("negative_prompt") or "").strip()
    fmt = {"1024x1024": "Square 1:1 image.", "1536x1024": "Wide landscape 3:2 image.", "1024x1536": "Tall portrait 2:3 image."}.get(_tamanho(body.get("size")), "")
    return p + (f"\nStyle: {est}." if est else "") + (f"\nAvoid: {neg[:400]}." if neg else "") + (f"\nFormat: {fmt}" if fmt else "")


async def _gerar(ctx, job: dict, body: dict) -> None:
    t0 = time.time()
    job["status"] = "running"
    try:
        r = await ctx.http.post(ctx.s.image_router_url.rstrip("/") + "/v1/images/generations",
                                json={"model": ctx.s.image_router_model, "prompt": _prompt(body), "n": 1, "size": _tamanho(body.get("size"))},
                                headers={"Authorization": f"Bearer {ctx.s.image_router_key}"}, timeout=TIMEOUT_S)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        d = (j.get("data") or [{}])[0] if r.status_code == 200 else {}
        if d.get("b64_json"):
            img = base64.b64decode(d["b64_json"])
        elif d.get("url"):
            img = (await ctx.http.get(d["url"], timeout=60)).content
        else:
            raise RuntimeError(f"router {r.status_code}: {str(j.get('error') or '')[:200]}")
        job.update(status="done", bytes=img, mime="image/png" if img[:4] == b"\x89PNG" else "image/jpeg" if img[:2] == b"\xff\xd8" else "image/webp",
                   seconds=round(time.time() - t0, 1), size=_tamanho(body.get("size")))
    except Exception as e:  # noqa: BLE001  (qualquer falha devolve a cota; a mensagem ao cliente e generica)
        log.warning("imagem %s falhou: %s", job["id"], str(e)[:300])
        mediaquota.undo(ctx.store, job.get("hold"))
        job.update(status="failed", error="falhou", hold=None, seconds=round(time.time() - t0, 1))


async def criar(ctx, person_id: int | None, hold, body: dict) -> dict:
    now = time.time()
    _limpar(now)
    job = {"id": "img-" + uuid.uuid4().hex[:20], "person_id": person_id, "status": "queued", "criado": now, "hold": hold, "bytes": None,
           "mime": None, "seconds": None, "size": None, "error": None}
    _jobs[job["id"]] = job
    tarefa = asyncio.create_task(_gerar(ctx, job, body))
    job["_task"] = tarefa
    if body.get("async"):
        return {"id": job["id"], "status": "queued", "eta_s": ETA_S}
    await tarefa
    if job["status"] != "done":
        return {"id": job["id"], "status": "failed"}
    return {"id": job["id"], "data": [{"b64_json": base64.b64encode(job["bytes"]).decode()}], "seconds": job["seconds"], "size": job["size"]}


def achar(job_id: str, person_id: int | None) -> dict | None:
    j = _jobs.get(job_id)
    if j is None or (person_id is not None and j["person_id"] not in (None, person_id)):
        return None
    return j


def progresso(j: dict) -> dict:
    gasto = time.time() - j["criado"]
    prog = 100 if j["status"] == "done" else min(95, int(gasto / ETA_S * 100)) if j["status"] == "running" else 0
    msg = {"queued": "Sua imagem está na fila para ser gerada.", "running": "Gerando a sua imagem...", "done": "Pronto.", "failed": "Não deu certo. Nada foi cobrado."}
    return {"id": j["id"], "kind": "image", "status": j["status"], "position": None, "eta_s": max(0, int(ETA_S - gasto)) if j["status"] != "done" else 0,
            "progress": prog, "message": msg[j["status"]], "mime": j["mime"], "seconds": j["seconds"], "size": j["size"], "error": j["error"]}


def estado(j: dict) -> dict:
    return {"id": j["id"], "state": {"queued": "queued", "running": "running", "done": "done", "failed": "failed"}[j["status"]], "mime": j["mime"],
            "seconds": j["seconds"], "size": j["size"], "error": j["error"]}
