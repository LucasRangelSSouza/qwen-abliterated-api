"""Ditado ao vivo (spec 49): fala x silencio do Fluxo do stt_server, sem carregar o Whisper."""
import array
import importlib
import math
import os
import sys
from pathlib import Path


def _carregar():
    os.environ["STT_SEM_MODELO"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "remote"))
    return importlib.import_module("stt_server")


def _som(seg, amp=6000, f=220):
    n = int(seg * 16000)
    return array.array("h", (int(amp * math.sin(2 * math.pi * f * i / 16000)) for i in range(n))).tobytes()


def _mudo(seg):
    return bytes(int(seg * 16000) * 2)


def test_fala_gera_parciais_e_silencio_fecha_a_frase():
    srv = _carregar()
    f = srv.Fluxo(endpointing_ms=300, interim_ms=800)
    acoes = f.alimentar(_mudo(0.5))
    assert acoes == [] and not f.falando
    acoes = []
    for _ in range(25):  # 2,5 s de fala em pedacos de 100 ms
        acoes += f.alimentar(_som(0.1))
    assert acoes.count("interim") == 3 and "final" not in acoes
    assert len(f.audio()) > 2 * 16000
    acoes = []
    for _ in range(4):  # 400 ms de silencio
        acoes += f.alimentar(_mudo(0.1))
    assert "final" in acoes
    a = f.fechar()
    assert len(a) > 2 * 16000 and not f.falando and len(f.fala) == 0


def test_frase_longa_demais_fecha_sozinha_e_ruido_baixo_nao_e_fala():
    srv = _carregar()
    f = srv.Fluxo(max_s=2.0)
    acoes = []
    for _ in range(30):
        acoes += f.alimentar(_som(0.1))
    assert "final" in acoes
    g = srv.Fluxo()
    assert g.alimentar(_som(1.0, amp=150)) == [] and not g.falando


def test_alucinacao_do_whisper_nao_vira_texto():
    srv = _carregar()
    assert srv.ALUCINACAO.match("Legendas pela comunidade Amara.org")
    assert srv.ALUCINACAO.match("Obrigado por assistir!")
    assert not srv.ALUCINACAO.match("Obrigado, pode mandar o arquivo")


def test_websocket_no_protocolo_interim_text_endpoint(monkeypatch):
    import json

    from starlette.testclient import TestClient

    srv = _carregar()
    chamadas = []
    monkeypatch.setattr(srv, "_decodificar", lambda audio, idioma: chamadas.append(len(audio)) or f"fala com {len(audio) // 1600} decimos")
    c = TestClient(srv.app)
    url = "/v1/audio/stream?encoding=linear16&sample_rate=16000&channels=1&endpointing_ms=300&utterance_end_ms=1000&language=pt"
    with c.websocket_connect(url, headers={"Authorization": "x", "x-app": "segundo-cerebro"}) as ws:
        ws.send_text(json.dumps({"type": "KeepAlive"}))
        for _ in range(20):
            ws.send_bytes(_som(0.1))
        for _ in range(5):
            ws.send_bytes(_mudo(0.1))
        tipos = []
        while True:
            m = json.loads(ws.receive_text())
            tipos.append(m["type"])
            if m["type"] == "TranscriptEndpoint":
                break
        assert "TranscriptInterim" in tipos and tipos[-2:] == ["TranscriptText", "TranscriptEndpoint"]
        ws.send_text(json.dumps({"type": "CloseStream"}))
    assert srv._estado["conexoes"] == 0


def test_websocket_recusa_formato_errado():
    import json

    from starlette.testclient import TestClient

    srv = _carregar()
    with TestClient(srv.app).websocket_connect("/v1/audio/stream?encoding=opus") as ws:
        assert json.loads(ws.receive_text())["type"] == "Error"
