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


# ---- voz de saida (/v1/audio/speech)
async def test_voz_exige_chave(rig: Rig):
    r = await rig.client.post("/v1/audio/speech", json={"input": "oi"})
    assert r.status_code == 401


async def test_voz_stream_e_escolha_de_voz(rig: Rig, monkeypatch):
    from app import voz
    pedidos = []

    async def falso(texto, v, rate="+0%"):
        pedidos.append((texto, v, rate))
        yield b"ID3aaa"
        yield b"bbb"
    monkeypatch.setattr(voz, "falar", falso)
    r = await rig.client.post("/v1/audio/speech", json={"input": "**Oi**, veja [aqui](http://x.com) 😀", "voice": "masculina", "speed": 1.2}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3aaabbb"
    assert pedidos == [("Oi , veja aqui", "pt-BR-AntonioNeural", "+20%")]


async def test_voz_padrao_e_nome_invalido(rig: Rig):
    from app import voz
    assert voz.escolher_voz(None) == voz.PADRAO
    assert voz.escolher_voz("rm -rf") == voz.PADRAO
    assert voz.escolher_voz("pt-BR-FranciscaNeural") == "pt-BR-FranciscaNeural"


async def test_voz_texto_vazio_e_grande(rig: Rig):
    r = await rig.client.post("/v1/audio/speech", json={"input": "  **  "}, headers=H)
    assert r.status_code == 400
    r = await rig.client.post("/v1/audio/speech", json={"input": "a " * 7000}, headers=H)
    assert r.status_code == 413


async def test_voz_falha_antes_do_audio_vira_502(rig: Rig, monkeypatch):
    from app import voz

    async def quebra(texto, v, rate="+0%"):
        raise RuntimeError("403 do edge")
        yield b""
    monkeypatch.setattr(voz, "falar", quebra)
    r = await rig.client.post("/v1/audio/speech", json={"input": "oi"}, headers=H)
    assert r.status_code == 502
