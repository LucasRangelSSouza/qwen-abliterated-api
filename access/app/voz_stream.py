"""Transcricao de voz ao vivo (spec 52): o app abre um WebSocket aqui com a chave do cliente e repassamos ao stream do 9Router
global com a chave do SERVIDOR (a chave do 9Router nunca vai para o app). Protocolo igual nos dois lados: frames binarios de PCM
16 bits 16 kHz mono, texto KeepAlive/CloseStream; volta TranscriptText (fala acumulada) e TranscriptEndpoint.

Regra do Lucas: conexao curta. O app troca a cada 2 min; aqui o corte e em LIMITE_S (folga de 10 s) para nada ficar pendurado.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, WebSocket

from app.proxy import _log, authenticate

router = APIRouter()
LIMITE_S = 130
PARAMETROS = ("encoding", "sample_rate", "channels", "endpointing_ms", "utterance_end_ms", "language", "use_conversation_engine")
log = logging.getLogger("uvicorn.error")


@router.websocket("/v1/audio/stream")
async def audio_stream(ws: WebSocket):
    ctx = ws.app.state.ctx
    try:
        p = authenticate(ctx, ws)
    except HTTPException as e:
        await ws.close(code=4401 if e.status_code == 401 else 4403)
        return
    url = ctx.s.stt_stream_url
    chave = ctx.s.stt_stream_key or ctx.s.image_router_key
    if not url or not chave:
        await ws.close(code=4503)
        return
    await ws.accept()
    q = {k: ws.query_params[k] for k in PARAMETROS if k in ws.query_params}
    t0 = time.time()
    audio = 0
    status = "ok"
    from websockets.asyncio.client import connect

    try:
        async with connect(url + "?" + urlencode(q), additional_headers={"Authorization": f"Bearer {chave}"}, open_timeout=10, max_size=2 ** 22) as up:

            async def subir():
                nonlocal audio
                while True:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        return
                    if m.get("bytes") is not None:
                        audio += len(m["bytes"])
                        await up.send(m["bytes"])
                    elif m.get("text") is not None:
                        await up.send(m["text"])

            async def descer():
                async for m in up:
                    if isinstance(m, str):
                        await ws.send_text(m)

            tarefas = [asyncio.create_task(subir()), asyncio.create_task(descer())]
            _, pendentes = await asyncio.wait(tarefas, timeout=LIMITE_S, return_when=asyncio.FIRST_COMPLETED)
            for t in pendentes:
                t.cancel()
            if len(pendentes) == 2:
                status = "limite"
    except Exception as e:  # noqa: BLE001  upstream fora: o app volta sozinho ao ditado por frases
        status = "upstream_error"
        log.info("voz stream: upstream falhou: %s", str(e)[:160])
        try:
            await ws.send_text('{"type": "Error", "data": "transcricao indisponivel agora"}')
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        segundos = audio / 32000
        _log(ctx, p, "voz_stream", 0, 0, 0, status, None)
        log.info("voz stream: %s %.1fs de audio em %.1fs (%s)", p.name, segundos, time.time() - t0, status)
