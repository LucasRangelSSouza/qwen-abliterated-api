"""Voz ao vivo (spec 52): /v1/audio/stream autentica o cliente e repassa ao stream do 9Router com a chave do SERVIDOR."""
import asyncio
import json
import threading

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from tests.conftest import OWNER_KEY, Rig

CHAVE_9ROUTER = "sk-9router-servidor"


class FalsoRouter:
    """Stream do 9Router de mentira, numa thread com loop próprio (o TestClient bloqueia o loop do teste)."""

    def __init__(self):
        self.visto = {"audio": 0, "textos": [], "auth": None, "path": None}
        self.porta = None
        self._pronto = threading.Event()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._rodar, daemon=True).start()
        self._pronto.wait(5)

    async def _tratar(self, ws):
        self.visto["auth"] = ws.request.headers.get("Authorization")
        self.visto["path"] = ws.request.path
        async for m in ws:
            if isinstance(m, bytes):
                self.visto["audio"] += len(m)
                if self.visto["audio"] >= 6400 and not self.visto.get("mandou"):
                    self.visto["mandou"] = True
                    await ws.send(json.dumps({"type": "TranscriptText", "data": "oi tudo bem"}))
            else:
                self.visto["textos"].append(json.loads(m)["type"])
                if json.loads(m)["type"] == "CloseStream":
                    await ws.send(json.dumps({"type": "TranscriptText", "data": "oi tudo bem?"}))
                    await ws.send(json.dumps({"type": "TranscriptEndpoint"}))
                    await ws.close()
                    return

    def _rodar(self):
        asyncio.set_event_loop(self._loop)

        async def principal():
            async with serve(self._tratar, "127.0.0.1", 0) as srv:
                self.porta = srv.sockets[0].getsockname()[1]
                self._pronto.set()
                await asyncio.Future()

        self._loop.run_until_complete(principal())


@pytest.fixture
async def rig_voz(tmp_path):
    falso = FalsoRouter()
    r = Rig(tmp_path)
    r.kw.update(stt_stream_url=f"ws://127.0.0.1:{falso.porta}/api/ws/speech_to_text/voice_stream", stt_stream_key=CHAVE_9ROUTER)
    await r.start()
    r.falso = falso
    yield r
    await r.stop()


def _conversar(app, headers):
    c = TestClient(app)
    recebido = []
    with c.websocket_connect("/v1/audio/stream?encoding=linear16&sample_rate=16000&language=pt&lixo=1", headers=headers) as ws:
        ws.send_bytes(b"\0" * 3200)
        ws.send_bytes(b"\0" * 3200)
        recebido.append(json.loads(ws.receive_text()))
        ws.send_text(json.dumps({"type": "KeepAlive"}))
        ws.send_text(json.dumps({"type": "CloseStream"}))
        recebido.append(json.loads(ws.receive_text()))
        recebido.append(json.loads(ws.receive_text()))
    return recebido


async def test_stream_repassa_com_chave_do_servidor(rig_voz):
    recebido = await asyncio.to_thread(_conversar, rig_voz.app, {"Authorization": f"Bearer {OWNER_KEY}"})
    v = rig_voz.falso.visto
    assert [m["type"] for m in recebido] == ["TranscriptText", "TranscriptText", "TranscriptEndpoint"]
    assert recebido[1]["data"] == "oi tudo bem?"
    assert v["auth"] == f"Bearer {CHAVE_9ROUTER}"  # a chave do cliente nunca vai para o 9Router
    assert "encoding=linear16" in v["path"] and "language=pt" in v["path"] and "lixo" not in v["path"]
    assert v["audio"] == 6400 and v["textos"] == ["KeepAlive", "CloseStream"]


def _sem_chave(app, headers):
    c = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/v1/audio/stream", headers=headers) as ws:
            ws.receive_text()
    return e.value.code


async def test_stream_recusa_chave_invalida(rig_voz):
    assert await asyncio.to_thread(_sem_chave, rig_voz.app, {}) == 4401
    assert await asyncio.to_thread(_sem_chave, rig_voz.app, {"Authorization": "Bearer chave-errada"}) == 4401
    assert rig_voz.falso.visto["auth"] is None  # nem chegou a abrir o 9Router
