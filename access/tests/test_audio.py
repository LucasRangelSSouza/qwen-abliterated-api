"""Ditado pelo llm-access: autentica, repassa o multipart ao llm-queue com a chave de servico e o nome de quem pediu."""
from tests.conftest import OWNER_KEY, UP_KEY, Rig

ARQ = {"file": ("fala.wav", b"RIFF....WAVEfmt ", "audio/wav")}
H = {"Authorization": f"Bearer {OWNER_KEY}"}


async def test_audio_exige_chave(rig: Rig):
    r = await rig.client.post("/v1/audio/transcriptions", files=ARQ)
    assert r.status_code == 401 and rig.up.ai_calls == 0


async def test_audio_repassa_com_chave_de_servico_e_nome(rig: Rig):
    r = await rig.client.post("/v1/audio/transcriptions", files=ARQ, data={"language": "pt"}, headers=H)
    assert r.status_code == 200 and r.json()["text"] == "ola mundo"
    path, body, headers = rig.up.calls[-1]
    assert path == "/v1/audio/transcriptions" and body["bytes"] > 0
    assert headers["authorization"] == f"Bearer {UP_KEY}" and headers["x-end-user"] == "dono"
    assert headers["content-type"].startswith("multipart/form-data")


async def test_audio_so_aceita_multipart(rig: Rig):
    r = await rig.client.post("/v1/audio/transcriptions", content=b"{}", headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 400 and rig.up.ai_calls == 0


async def test_audio_grande_demais(rig: Rig):
    r = await rig.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"0" * (26 * 1024 * 1024), "audio/wav")}, headers=H)
    assert r.status_code == 413 and rig.up.ai_calls == 0


async def test_audio_erro_do_upstream_chega_ao_cliente(rig: Rig):
    import httpx

    async def h(request, body):
        return httpx.Response(503, json={"detail": {"error": {"code": "gpu_unavailable"}}})
    rig.up.handler = h
    r = await rig.client.post("/v1/audio/transcriptions", files=ARQ, headers=H)
    assert r.status_code == 503
