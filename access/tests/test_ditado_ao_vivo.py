"""Ditado ao vivo (spec 49): /v1/audio/stream autentica a chave do cliente (com ou sem Bearer) e repassa o WebSocket ao llm-queue."""
import json
import socket
import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import OWNER_KEY, PASSWORD, PLATFORM_KEY, UP_ADMIN, UP_KEY, sha

visto = {}


async def fila_falsa(ws):
    visto["auth"] = ws.headers.get("authorization")
    visto["query"] = ws.url.query
    await ws.accept()
    pcm = await ws.receive_bytes()
    await ws.send_text(json.dumps({"type": "TranscriptInterim", "data": f"{len(pcm)} bytes"}))
    assert json.loads(await ws.receive_text())["type"] == "CloseStream"
    await ws.send_text(json.dumps({"type": "TranscriptText", "data": "ola"}))
    await ws.send_text(json.dumps({"type": "TranscriptEndpoint"}))
    await ws.close()


@pytest.fixture(scope="module")
def fila():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(Starlette(routes=[WebSocketRoute("/v1/audio/stream", fila_falsa)]), host="127.0.0.1", port=porta, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True


def _cliente(tmp_path, upstream):
    s = Settings(db_path=str(tmp_path / "a.db"), upstream_url=upstream, upstream_key=UP_KEY, upstream_admin_key=UP_ADMIN, admin_password=PASSWORD,
                 session_secret="x", owner_key_sha256=sha(OWNER_KEY), platform_key_sha256=sha(PLATFORM_KEY), panel_dir=str(tmp_path / "p"))
    return TestClient(create_app(s))


URL = "/v1/audio/stream?encoding=linear16&sample_rate=16000&channels=1&endpointing_ms=300&utterance_end_ms=1000&language=pt&use_conversation_engine=true"


@pytest.mark.parametrize("auth", [OWNER_KEY, f"Bearer {OWNER_KEY}"])
def test_repassa_no_protocolo_com_a_chave_interna(tmp_path, fila, auth):
    with _cliente(tmp_path, fila) as c, c.websocket_connect(URL, headers={"Authorization": auth, "x-app": "segundo-cerebro"}) as ws:
        ws.send_bytes(b"\x00\x01" * 1600)
        assert json.loads(ws.receive_text()) == {"type": "TranscriptInterim", "data": "3200 bytes"}
        ws.send_text(json.dumps({"type": "CloseStream"}))
        assert json.loads(ws.receive_text())["type"] == "TranscriptText"
        assert json.loads(ws.receive_text())["type"] == "TranscriptEndpoint"
    assert visto["auth"] == f"Bearer {UP_KEY}"  # o queue recebe a chave do access, nunca a do cliente
    assert "endpointing_ms=300" in visto["query"] and "language=pt" in visto["query"]


def test_chave_invalida_recusa_sem_chamar_o_queue(tmp_path, fila):
    visto.clear()
    with _cliente(tmp_path, fila) as c, c.websocket_connect(URL, headers={"Authorization": "sk-errada"}) as ws:
        assert json.loads(ws.receive_text()) == {"type": "Error", "data": "chave de API invalida"}
    assert "auth" not in visto
