"""Ditado: /v1/audio/transcriptions repassa o audio ao Whisper da maquina do LLM, sem acordar a GPU nem entrar na fila do chat."""
import httpx
import pytest
import pytest_asyncio

from app.config import Settings
from app.main import _forwards, create_app
from tests.test_api import CONSUMERS, H, StubCtl, make_upstream

AUDIO = {"file": ("fala.wav", b"RIFF....WAVEfmt ", "audio/wav")}


def montar(redis, handler, **kw):
    ctl = StubCtl()
    s = Settings(tunnel_mode="none", stt_enabled=kw.pop("stt", True), **kw)
    return ctl, create_app(s, controller=ctl, redis=redis, http=make_upstream(handler), consumers=CONSUMERS)


@pytest_asyncio.fixture
async def rig(redis):
    visto = {}

    def h(request: httpx.Request):
        visto["url"] = str(request.url)
        visto["ctype"] = request.headers.get("content-type", "")
        visto["bytes"] = len(request.content)
        return httpx.Response(200, json={"text": "ola mundo", "language": "pt"})

    ctl, app = montar(redis, h)
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        yield client, ctl, visto
        await client.aclose()


async def test_exige_chave(rig):
    client, _, _ = rig
    r = await client.post("/v1/audio/transcriptions", files=AUDIO)
    assert r.status_code == 401


async def test_repassa_o_multipart_ao_whisper(rig):
    client, _, visto = rig
    r = await client.post("/v1/audio/transcriptions", files=AUDIO, data={"language": "pt"}, headers=H)
    assert r.status_code == 200 and r.json()["text"] == "ola mundo"
    assert visto["url"].endswith("/v1/audio/transcriptions") and visto["ctype"].startswith("multipart/form-data") and visto["bytes"] > 0


async def test_gpu_fora_do_ar_responde_503_sem_acordar(rig):
    client, ctl, visto = rig
    ctl.state = "OFF"
    r = await client.post("/v1/audio/transcriptions", files=AUDIO, headers=H)
    assert r.status_code == 503 and r.json()["detail"]["error"]["code"] == "gpu_unavailable"
    assert not ctl.woken and "url" not in visto


async def test_audio_grande_demais(redis):
    ctl, app = montar(redis, lambda req: httpx.Response(200, json={"text": ""}), stt_max_mb=1)
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"0" * (2 * 1024 * 1024), "audio/wav")}, headers=H)
        assert r.status_code == 413 and r.json()["detail"]["error"]["code"] == "audio_too_large"
        await client.aclose()


async def test_desligado_por_padrao(redis):
    ctl, app = montar(redis, lambda req: httpx.Response(200, json={"text": ""}), stt=False)
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await client.post("/v1/audio/transcriptions", files=AUDIO, headers=H)
        assert r.status_code == 503 and r.json()["detail"]["error"]["code"] == "stt_disabled"
        await client.aclose()


async def test_upstream_caido_vira_502(redis):
    def h(request):
        raise httpx.ConnectError("sem tunel")
    ctl, app = montar(redis, h)
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await client.post("/v1/audio/transcriptions", files=AUDIO, headers=H)
        assert r.status_code == 502 and r.json()["detail"]["error"]["code"] == "upstream_error"
        await client.aclose()


def test_tunel_inclui_a_porta_da_transcricao_so_quando_ligada():
    assert (18002, 18002) in _forwards(Settings(tunnel_mode="none", stt_enabled=True))
    assert all(r != 18002 for _, r in _forwards(Settings(tunnel_mode="none")))


# ---- instalacao do Whisper por SSH (nunca pelo onstart: o spec_id nao pode mudar)

def test_comando_de_instalacao_e_idempotente_e_leva_o_servidor():
    import base64
    from app.gpu import stt
    cmd = stt.command(8)
    assert "STT_THREADS=8" in cmd and "STT_SERVER_B64=" in cmd
    assert "/health" in stt.PROVISION and "127.0.0.1" in stt.PROVISION and "nice -n 15" in stt.PROVISION
    b64 = cmd.split("STT_SERVER_B64='")[1].split("'")[0]
    assert b"faster_whisper" in base64.b64decode(b64)
    # o texto do script NAO pode viajar literal na linha de comando (o pgrep -f dele casaria com ele mesmo e ele se mataria)
    assert "uvicorn stt_server" not in cmd and "provision.sh" in cmd


async def test_garante_o_whisper_uma_vez_por_endereco(redis):
    from app.alerts import Alerts
    from app.gpu.controller import GPUController
    from app.gpu.ledger import Ledger
    from tests.fakes import FakeVast
    chamadas = []

    class T:
        async def open(self, h, p): return True
        async def healthy(self): return True
        async def run(self, h, p, cmd, timeout=90.0):
            chamadas.append((h, p, timeout, "stt_server" in cmd or "STT_SERVER_B64" in cmd))
            return True
        async def close(self): pass

    async def probe(): return True
    s = Settings(tunnel_mode="ssh", stt_enabled=True)
    ctl = GPUController(s, FakeVast(), T(), Ledger(redis), Alerts(""), redis, health_probe=probe, onstart="#!/bin/bash\n")
    antes = ctl.spec_id
    ctl._ensure_stt("1.2.3.4", 2222)
    ctl._ensure_stt("1.2.3.4", 2222)  # mesmo endereco: nao repete
    await ctl._stt_task
    ctl._ensure_stt("5.6.7.8", 2222)  # maquina nova: instala de novo
    await ctl._stt_task
    assert [(c[0], c[3]) for c in chamadas] == [("1.2.3.4", True), ("5.6.7.8", True)] and chamadas[0][2] >= 900
    assert ctl.spec_id == antes  # o fingerprint da instancia nao depende da transcricao
    await ctl.stop_loop()


async def test_desligado_nao_instala_nada(redis):
    from app.alerts import Alerts
    from app.gpu.controller import GPUController
    from app.gpu.ledger import Ledger
    from tests.fakes import FakeVast

    class T:
        async def run(self, *a, **k): raise AssertionError("nao devia rodar")
        async def close(self): pass

    async def probe(): return True
    ctl = GPUController(Settings(tunnel_mode="ssh", stt_enabled=False), FakeVast(), T(), Ledger(redis), Alerts(""), redis, health_probe=probe, onstart="#!/bin/bash\n")
    ctl._ensure_stt("1.2.3.4", 22)
    assert ctl._stt_done is None
    await ctl.stop_loop()
