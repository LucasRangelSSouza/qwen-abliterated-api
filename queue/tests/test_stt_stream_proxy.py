"""Ditado ao vivo (spec 49): o llm-queue repassa o WebSocket ao stt_server (aqui um stt_server de verdade com decodificador falso)."""
import importlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_api import CONSUMERS, H, StubCtl, make_upstream
from tests.test_stt_fluxo import _mudo, _som


@pytest.fixture(scope="module")
def voz():
    os.environ["STT_SEM_MODELO"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote"))
    srv = importlib.import_module("stt_server")
    srv._decodificar = lambda audio, idioma: "ola mundo"
    s = socket.socket(); s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=porta, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    server.should_exit = True


def _app(redis, upstream, **kw):
    s = Settings(tunnel_mode="none", stt_enabled=True, stt_upstream=upstream, **kw)
    return create_app(s, controller=StubCtl(), redis=redis, http=make_upstream(lambda r: None), consumers=CONSUMERS)


URL = "/v1/audio/stream?encoding=linear16&sample_rate=16000&channels=1&endpointing_ms=300&language=pt"


def test_repasse_nos_dois_sentidos(voz, redis):
    with TestClient(_app(redis, voz)) as c, c.websocket_connect(URL, headers=H) as ws:
        for _ in range(15):
            ws.send_bytes(_som(0.1))
        for _ in range(5):
            ws.send_bytes(_mudo(0.1))
        tipos = []
        while not tipos or tipos[-1] != "TranscriptEndpoint":
            m = json.loads(ws.receive_text())
            tipos.append(m["type"])
            if m["type"] == "TranscriptText":
                assert m["data"] == "ola mundo"
        assert "TranscriptText" in tipos
        ws.send_text(json.dumps({"type": "CloseStream"}))


def test_chave_errada_recusa(voz, redis):
    with TestClient(_app(redis, voz)) as c, c.websocket_connect(URL, headers={"Authorization": "Bearer errada"}) as ws:
        assert json.loads(ws.receive_text()) == {"type": "Error", "data": "chave de API invalida"}
