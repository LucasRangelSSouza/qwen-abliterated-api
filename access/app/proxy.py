"""Rotas publicas (API da OpenAI): autentica, reserva saldo, repassa ao llm-queue byte a byte e reconcilia pelo `usage`."""
from __future__ import annotations

import asyncio
import hmac
import json
import math
import sqlite3

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.auth import Principal, bearer, sha256
from app.ctx import Ctx, err, fail
from app.ledger import Blocked, iso
from app.tokens import Meter, estimate_in, max_out

router = APIRouter()

# cabecalhos do upstream que seguem para o cliente (espera da GPU e classe da fila)
PASS_BACK = {"x-llm-state", "x-llm-queue-position", "retry-after", "x-queue-class", "x-queue-wait", "x-queue-notice",
             "x-end-user", "cache-control", "x-accel-buffering"}
PASS_UP = ("x-queue-class", "x-queue-notice", "x-end-user")


def authenticate(ctx: Ctx, request: Request) -> Principal:
    key = bearer(request.headers)
    digest = sha256(key)
    for kind, want in (("owner", ctx.s.owner_key_sha256), ("platform", ctx.s.platform_key_sha256)):
        if key and want and hmac.compare_digest(digest, want):
            return Principal(kind, name=kind)  # sem banco: valem mesmo que o arquivo falhe
    try:
        r = ctx.store.one("SELECT k.id key_id, p.id, p.name, p.status FROM keys k JOIN people p ON p.id=k.person_id "
                          "WHERE k.key_hash=? AND k.revoked_at IS NULL", (digest,))
    except sqlite3.Error:
        raise fail(503, "banco de chaves indisponivel", "service_unavailable", "database_unavailable")
    if not r:
        raise fail(401, "chave de API invalida", "invalid_request_error", "invalid_api_key")
    if r["status"] != "active":
        raise fail(403, "conta suspensa", "invalid_request_error", "account_suspended")
    return Principal("person", r["id"], r["name"], r["key_id"], r["status"])


def _ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def _log(ctx: Ctx, p: Principal, route: str, i: int, o: int, t: int, status: str, limits: dict | None) -> None:
    try:
        ctx.ledger.log(person_id=p.person_id, key_id=p.key_id, actor=None if p.kind == "person" else p.kind, route=route,
                       in_tokens=i, out_tokens=o, total=t, status=status, limits=limits)
    except Exception:  # noqa: BLE001  registro nunca derruba o servico (chaves especiais sem banco)
        pass


def _blocked(ctx: Ctx, b: Blocked) -> JSONResponse:
    now = ctx.clock()
    falta = b.needed - b.remaining
    when = f" A janela reabre em {iso(b.resets_at)}." if b.resets_at else ""
    msg = (f"Limite de uso da janela {b.window} ({b.limit} tokens): restam {b.remaining}, este pedido reserva {b.needed} "
           f"(faltam {falta}); reduza max_tokens ou aguarde.{when}")
    headers = {"Retry-After": str(max(1, math.ceil(b.resets_at - now)))} if b.resets_at else {}
    body = err(msg, "rate_limit_error", "usage_limit_reached", window=b.window, resets_at=iso(b.resets_at),
               remaining_tokens=b.remaining, requested_tokens=b.needed, missing_tokens=falta)
    return JSONResponse(body, status_code=429, headers=headers)


def _pass_headers(h: httpx.Headers) -> dict:
    return {k: v for k, v in h.items() if k.lower() in PASS_BACK}


async def forward(request: Request, path: str, route: str) -> Response:
    ctx = _ctx(request)
    p = authenticate(ctx, request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:  # noqa: BLE001
        raise fail(400, "JSON invalido", "invalid_request_error", "invalid_json")
    stream = bool(body.get("stream")) and route == "chat"
    in_est = estimate_in(body, route)
    reserve = in_est + (max_out(body, ctx.s.default_max_tokens) if route == "chat" else 0)
    if stream:
        so = body.get("stream_options") if isinstance(body.get("stream_options"), dict) else {}
        body["stream_options"] = {**so, "include_usage": True}

    res, limits = None, None
    if p.kind == "person":
        try:
            limits = ctx.ledger.limits(p.person_id)
            res = ctx.ledger.reserve(p.person_id, reserve)
        except Blocked as b:
            _log(ctx, p, route, 0, 0, 0, "blocked_" + b.window, limits)
            return _blocked(ctx, b)
        except sqlite3.Error:
            raise fail(503, "banco de chaves indisponivel", "service_unavailable", "database_unavailable")

    def release_and_log(status: str) -> None:
        if res:
            ctx.ledger.release(res)
        _log(ctx, p, route, 0, 0, 0, status, limits)

    headers = {"Content-Type": "application/json"}
    if ctx.s.upstream_key:
        headers["Authorization"] = f"Bearer {ctx.s.upstream_key}"
    for h in PASS_UP:
        if h in request.headers:
            headers[h] = request.headers[h]
    try:
        req = ctx.http.build_request("POST", ctx.s.upstream_base + path, json=body, headers=headers)
        up = await ctx.http.send(req, stream=True)
    except httpx.HTTPError:
        release_and_log("upstream_error")
        return JSONResponse(err("falha ao falar com o servico de IA", "server_error", "upstream_error"), status_code=502)
    except asyncio.CancelledError:
        release_and_log("cancelled")
        raise

    if up.status_code != 200:  # erro antes de qualquer dado real: nao abre janela, nao cobra
        try:
            data = await up.aread()
        finally:
            await up.aclose()
        release_and_log("upstream_error")
        return Response(data, status_code=up.status_code, headers=_pass_headers(up.headers),
                        media_type=up.headers.get("content-type", "application/json"))

    meter = Meter(stream)

    async def gen():
        completed = False
        try:
            async for chunk in _chunks(up):
                meter.feed(chunk)
                yield chunk
            completed = True
        except httpx.HTTPError:
            msg = err("conexao com o servico de IA interrompida", "server_error", "upstream_error")
            yield b"data: " + json.dumps(msg).encode() + b"\n\ndata: [DONE]\n\n" if stream else json.dumps(msg).encode()
        finally:  # roda tambem se o cliente desconectar: cobra so o que foi entregue
            meter.finish()
            _finalize(ctx, p, res, limits, route, meter, in_est, reserve, completed)
            try:
                await asyncio.shield(up.aclose())
            except BaseException:  # noqa: BLE001
                pass

    media = up.headers.get("content-type") or ("text/event-stream" if stream else "application/json")
    return StreamingResponse(gen(), media_type=media, headers=_pass_headers(up.headers))


async def _chunks(up: httpx.Response):
    if up.is_stream_consumed:  # corpo ja lido (transporte que entrega tudo de uma vez)
        yield up.content
        return
    async for c in up.aiter_raw():
        yield c


def _finalize(ctx, p, res, limits, route, meter: Meter, in_est, reserve, completed) -> None:
    try:
        if not meter.real:  # so erro ou aviso de espera: nada foi entregue
            if res:
                ctx.ledger.release(res)
            _log(ctx, p, route, 0, 0, 0, "upstream_error" if (meter.error or completed) else "cancelled", limits)
            return
        i, o, t, status = meter.settle(in_est, reserve, completed)
        if res:
            ctx.ledger.commit(res, t)
        _log(ctx, p, route, i, o, t, status, limits)
    except Exception:  # noqa: BLE001
        pass


@router.post("/v1/chat/completions")
async def chat(request: Request):
    return await forward(request, "/v1/chat/completions", "chat")


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    return await forward(request, "/v1/embeddings", "embed")


@router.get("/v1/models")
async def models(request: Request):
    ctx = _ctx(request)
    authenticate(ctx, request)
    try:
        r = await ctx.http.get(ctx.s.upstream_base + "/v1/models",
                               headers={"Authorization": f"Bearer {ctx.s.upstream_key}"} if ctx.s.upstream_key else {}, timeout=15)
    except httpx.HTTPError:
        raise fail(502, "falha ao falar com o servico de IA", "server_error", "upstream_error")
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@router.get("/v1/usage")
async def usage(request: Request):
    ctx = _ctx(request)
    p = authenticate(ctx, request)
    if p.kind != "person":
        free = {"limit": None, "used": 0, "remaining": None, "resets_at": None}
        return {"person": p.name, "session": free, "week": dict(free)}
    try:
        w = ctx.ledger.windows(p.person_id)
    except sqlite3.Error:
        raise fail(503, "banco de chaves indisponivel", "service_unavailable", "database_unavailable")
    return {"person": p.name, "session": w["session"], "week": w["week"]}
