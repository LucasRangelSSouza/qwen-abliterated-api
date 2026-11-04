"""Caminho do NIVEL 2 (com classificadores). Este modulo so e importado quando uma pessoa de nivel 2 faz um pedido:
quem e de nivel 1 nunca chega aqui (ver `app.proxy`). Fluxo do chat:
  1. classificador 1 (entrada) le o pedido; se barrar, a pessoa recebe uma recusa padronizada e nada chega ao modelo;
  2. o pedido vai ao modelo SEM streaming (precisa da resposta inteira, inclusive da chamada de ferramenta, para o classificador 2 ler);
  3. classificador 2 (saida/acao) le a resposta e as chamadas de ferramenta; se barrar, a resposta e trocada pela recusa;
  4. so entao entrega (e, se a pessoa pediu stream, remonta o stream a partir da resposta ja aprovada).
O custo e latencia (a resposta so aparece quando termina): por isso o nivel 1 fica de fora."""
from __future__ import annotations

import json
import time

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.ctx import Ctx, err
from app.ledger import Blocked
from app.safety import classifier_input, classifier_output
from app.safety.common import Verdict, load_config, log_block
from app.tokens import estimate_in, max_out


def _refusal_text(ctx: Ctx, classifier: str, v: Verdict) -> str:
    mod = classifier_input if classifier == "input" else classifier_output
    return v.refusal(load_config(ctx.store, mod.CONFIG_KEY, mod.DEFAULTS).get("refusal") or None)


def _completion(text: str, model: str, usage: dict | None = None) -> dict:
    return {"id": f"chatcmpl-safety-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _sse(data: dict, include_usage: bool):
    """Remonta um stream a partir de uma resposta ja aprovada (conteudo e chamadas de ferramenta de uma vez)."""
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    base = {"id": data.get("id"), "object": "chat.completion.chunk", "created": data.get("created"), "model": data.get("model")}

    def chunk(delta, finish=None, usage=None):
        o = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if usage is None else []}
        if usage is not None:
            o["usage"] = usage
        return b"data: " + json.dumps(o, ensure_ascii=False).encode() + b"\n\n"

    async def gen():
        delta = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(msg["tool_calls"])]
        yield chunk(delta)
        yield chunk({}, finish=ch.get("finish_reason") or "stop")
        if include_usage:
            yield chunk(None, usage=data.get("usage") or {}, )
        yield b"data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


def refusal_response(body: dict, text: str, which: str) -> Response:
    data = _completion(text, str(body.get("model") or "qwen-abliterated"))
    headers = {"x-safety-blocked": which}
    if body.get("stream"):
        r = _sse(data, bool((body.get("stream_options") or {}).get("include_usage")))
        r.headers.update(headers)
        return r
    return JSONResponse(data, headers=headers)


async def forward_level2(ctx: Ctx, request, p, body: dict) -> Response:
    """Chat de uma pessoa de nivel 2."""
    from app.proxy import PASS_UP, _blocked, _log, _pass_headers  # import tardio: proxy carrega este modulo so no nivel 2

    v = await classifier_input.check(ctx, body.get("messages") or [])
    if v.blocked:
        log_block(ctx.store, "input", p.person_id, v.category, v.score, "chat", v.source, ctx.clock())
        _log(ctx, p, "chat", 0, 0, 0, "safety_input", None)
        return refusal_response(body, _refusal_text(ctx, "input", v), "input")

    in_est = estimate_in(body, "chat")
    reserve = in_est + max_out(body, ctx.s.default_max_tokens)
    limits = ctx.ledger.limits(p.person_id)
    try:
        res = ctx.ledger.reserve(p.person_id, reserve)
    except Blocked as b:
        _log(ctx, p, "chat", 0, 0, 0, "blocked_" + b.window, limits)
        return _blocked(ctx, b)

    headers = {"Content-Type": "application/json"}
    if ctx.s.upstream_key:
        headers["Authorization"] = f"Bearer {ctx.s.upstream_key}"
    for h in PASS_UP:
        if h in request.headers:
            headers[h] = request.headers[h]
    headers["x-end-user"] = p.name.encode("ascii", "replace").decode()[:60]
    body2 = {k: val for k, val in body.items() if k not in ("stream", "stream_options")}
    body2["stream"] = False
    try:
        up = await ctx.http.post(ctx.s.upstream_base + "/v1/chat/completions", json=body2, headers=headers, timeout=ctx.s.request_timeout_s)
    except httpx.HTTPError:
        ctx.ledger.release(res)
        _log(ctx, p, "chat", 0, 0, 0, "upstream_error", limits)
        return JSONResponse(err("falha ao falar com o servico de IA", "server_error", "upstream_error"), status_code=502)
    if up.status_code != 200:
        ctx.ledger.release(res)
        _log(ctx, p, "chat", 0, 0, 0, "upstream_error", limits)
        return Response(up.content, status_code=up.status_code, headers=_pass_headers(up.headers), media_type=up.headers.get("content-type", "application/json"))
    try:
        data = up.json()
        msg = data["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        ctx.ledger.release(res)
        return JSONResponse(err("resposta invalida do servico de IA", "server_error", "upstream_error"), status_code=502)

    out = await classifier_output.check(ctx, msg.get("content"), msg.get("tool_calls"))
    blocked_by = None
    if out.blocked:
        blocked_by = "output"
        log_block(ctx.store, "output", p.person_id, out.category, out.score, "chat", out.source, ctx.clock())
        data["choices"][0]["message"] = {"role": "assistant", "content": _refusal_text(ctx, "output", out)}
        data["choices"][0]["finish_reason"] = "stop"

    usage = data.get("usage") or {}
    total = int(usage.get("total_tokens") or 0) or (in_est + 1)
    ctx.ledger.commit(res, total)  # os tokens foram gerados mesmo que a resposta tenha sido barrada
    _log(ctx, p, "chat", int(usage.get("prompt_tokens") or in_est), int(usage.get("completion_tokens") or 0), total, "safety_output" if blocked_by else "ok", limits)
    extra = {"x-safety-blocked": blocked_by} if blocked_by else {}
    if body.get("stream"):
        r = _sse(data, bool((body.get("stream_options") or {}).get("include_usage")))
        r.headers.update(extra)
        return r
    return JSONResponse(data, headers=extra)


async def gate_image(ctx: Ctx, p, body: dict) -> Response | None:
    """Pedido de imagem de uma pessoa de nivel 2: o classificador 1 le o prompt. Devolve a recusa (422) ou None para seguir."""
    v = await classifier_input.check(ctx, [{"role": "user", "content": str(body.get("prompt") or "")}])
    if not v.blocked:
        return None
    log_block(ctx.store, "input", p.person_id, v.category, v.score, "image", v.source, ctx.clock())
    return JSONResponse(err(_refusal_text(ctx, "input", v), "invalid_request_error", "safety_blocked"), status_code=422, headers={"x-safety-blocked": "input"})
