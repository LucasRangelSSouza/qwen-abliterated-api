"""Rotas antigas de midia do app atendidas pela Runware (MEDIA_BACKEND=runware): mesmo contrato de job, preco do Estudio, em breve onde nao ha modelo."""
import base64
import struct

import httpx
import pytest_asyncio

from app import midia_runware
from tests.conftest import OWNER_KEY, Rig

H = {"Authorization": f"Bearer {OWNER_KEY}"}


def mp4_falso(segundos: float) -> bytes:
    mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + b"\x00" * 8 + struct.pack(">II", 1000, int(segundos * 1000))
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8 + struct.pack(">I", len(mvhd) + 4) + mvhd + b"\x00" * 64


@pytest_asyncio.fixture
async def rig_m(tmp_path):
    r = Rig(tmp_path, runware_api_key="rw-teste", runware_url="http://up/runware", media_backend="runware")
    await r.start()
    r.up.rw = []
    r.up.fila = []

    async def handler(request: httpx.Request, body):
        if request.url.path == "/runware":
            r.up.rw.append(body)
            t = body[0]
            if t["taskType"] == "mediaStorage":
                return httpx.Response(200, json={"data": [{"mediaUUID": "m-%d" % len(r.up.rw)}]})
            if r.up.fila:
                return httpx.Response(200, json=r.up.fila.pop(0))
            return httpx.Response(200, json={"data": [{"taskUUID": t["taskUUID"], "status": "processing"}]})
        if request.url.path == "/arquivo.mp4":
            return httpx.Response(200, content=b"VIDEO-PRONTO", headers={"content-type": "video/mp4"})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


def test_duracao_mp4():
    assert midia_runware.duracao_mp4(mp4_falso(12.5)) == 12.5
    assert midia_runware.duracao_mp4(b"nada") is None


async def test_troca_de_personagem_vai_para_a_runware_com_duracao_do_video(rig_m: Rig):
    v = base64.b64encode(mp4_falso(8)).decode()
    f = base64.b64encode(b"\xff\xd8\xff\xe0foto").decode()
    r = await rig_m.client.post("/v1/videos/generations", json={"recipe": "openjutsu-swap", "video": v, "image": f, "resolution": "480p", "async": True}, headers=H)
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["status"] == "queued" and j["preco_usd"] == 2.0  # 8 s x US$ 0,05 x 5
    up_v, up_f, tarefa = rig_m.up.rw
    assert up_v[0]["media"].startswith("data:video/mp4;base64,") and up_f[0]["media"].startswith("data:image/jpeg;base64,")
    assert tarefa[0]["model"] == "prunaai:p-video@2-pro-replace" and tarefa[0]["resolution"] == "480p"
    # acompanhamento pelo mesmo caminho do app
    s = (await rig_m.client.get(f"/v1/media/jobs/{j['id']}", headers=H)).json()
    assert s["status"] == "running" and "Estúdio" in s["message"]
    rig_m.up.fila.append({"data": [{"taskUUID": j["id"], "videoURL": "https://mock.runware.local/arquivo.mp4", "cost": 0.4}]})
    s = (await rig_m.client.get(f"/v1/media/jobs/{j['id']}", headers=H)).json()
    assert s["status"] == "done" and s["progress"] == 100
    f = await rig_m.client.get(f"/v1/jobs/{j['id']}/file", headers=H)
    assert f.status_code == 200 and f.content.startswith(b"\x00\x00\x00\x18ftyp") and f.headers["content-type"] == "video/mp4"


async def test_video_por_texto_alta_e_rapida(rig_m: Rig):
    r = await rig_m.client.post("/v1/videos/generations", json={"prompt": "a red car", "format": "retrato", "seconds": 30, "recipe": "wan22-a14b", "async": True}, headers=H)
    t = rig_m.up.rw[-1][0]
    assert r.status_code == 202 and t["model"] == "alibaba:wan@3.0" and t["resolution"] == "720p" and (t["width"], t["height"]) == (720, 1280) and t["duration"] == 15
    r = await rig_m.client.post("/v1/videos/generations", json={"prompt": "a red car", "recipe": "ltx-video", "seconds": 4, "image": base64.b64encode(b"\x89PNG....").decode(), "async": True}, headers=H)
    t = rig_m.up.rw[-1][0]
    assert t["model"] == "prunaai:p-video@2-pro" and t["inputs"]["frameImages"] == ["m-%d" % (len(rig_m.up.rw) - 1)]


async def test_clone_de_voz_e_em_breve(rig_m: Rig):
    r = await rig_m.client.post("/v1/audio/voice-clone", json={"text": "Olá", "language": "pt", "reference_audio": base64.b64encode(b"RIFF....WAVEfmt ").decode()}, headers=H)
    t = rig_m.up.rw[-1][0]
    assert r.status_code == 202 and t["model"] == "heygen:voice@1.0" and t["speech"]["text"] == "Olá"
    r = await rig_m.client.post("/v1/videos/dub", json={"video": "AAAA", "target_language": "en"}, headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "estudio_em_breve"
    assert not [c for c in rig_m.up.calls if c[0].startswith("/v1/videos")]  # a GPU de midia nunca e chamada
