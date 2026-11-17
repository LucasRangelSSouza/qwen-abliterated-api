"""Plano B da transcrição: GPU fora -> Whisper na CPU da VPS, no mesmo formato da API."""
import httpx
import pytest_asyncio

from app import stt_local
from tests.conftest import OWNER_KEY, Rig

H = {"Authorization": f"Bearer {OWNER_KEY}"}


@pytest_asyncio.fixture
async def rig(tmp_path):
    r = Rig(tmp_path)
    await r.start()

    async def handler(request: httpx.Request, body):
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(503, json={"detail": "gpu_unavailable"})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


async def test_gpu_fora_transcreve_na_cpu(rig: Rig, monkeypatch):
    vistos = {}

    def falso(audio, nome, idioma, verbose):
        vistos.update(n=len(audio), nome=nome, idioma=idioma, verbose=verbose)
        return {"text": "olá mundo"} if not verbose else {"text": "olá mundo", "segments": [{"id": 0, "start": 0, "end": 1, "text": "olá mundo"}]}
    monkeypatch.setattr(stt_local, "transcrever", falso)
    r = await rig.client.post("/v1/audio/transcriptions", headers=H, files={"file": ("a.webm", b"x" * 2000, "audio/webm")},
                              data={"model": "whisper", "language": "pt", "response_format": "verbose_json"})
    assert r.status_code == 200 and r.json()["text"] == "olá mundo" and r.headers.get("x-stt") == "cpu"
    assert vistos == {"n": 2000, "nome": "a.webm", "idioma": "pt", "verbose": True}
