"""Imagem pelo ChatGPT no 9Router global: mesmo contrato do app (async -> progresso -> arquivo), cota reservada e devolvida na falha."""
import asyncio
import base64

import httpx
import pytest_asyncio

from tests.conftest import OWNER_KEY, Rig

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
H = {"Authorization": f"Bearer {OWNER_KEY}"}


@pytest_asyncio.fixture
async def rig_img(tmp_path):
    r = Rig(tmp_path, image_router_url="http://up/router", image_router_key="r9-chave")
    await r.start()
    r.up.router_calls = []
    r.up.router_falha = False

    async def handler(request: httpx.Request, body):
        if request.url.path == "/router/v1/images/generations":
            assert request.headers["authorization"] == "Bearer r9-chave"
            r.up.router_calls.append(body)
            if r.up.router_falha:
                return httpx.Response(502, json={"error": {"message": "codex caiu"}})
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


async def _esperar(rig, jid):
    for _ in range(50):
        s = (await rig.client.get(f"/v1/media/jobs/{jid}", headers=H)).json()
        if s["status"] in ("done", "failed"):
            return s
        await asyncio.sleep(0.02)
    raise AssertionError("nao terminou")


async def test_async_igual_ao_app(rig_img: Rig):
    r = await rig_img.client.post("/v1/images/generations", json={"prompt": "um hamburguer artesanal", "size": "1792x1024", "style": "foto", "async": True}, headers=H)
    assert r.status_code == 202 and r.json()["id"].startswith("img-")
    jid = r.json()["id"]
    s = await _esperar(rig_img, jid)
    assert s["status"] == "done" and s["mime"] == "image/png" and s["progress"] == 100
    corpo = rig_img.up.router_calls[0]
    assert corpo["model"] == "cx/gpt-image-2" and corpo["size"] == "1536x1024" and "photorealistic" in corpo["prompt"] and "landscape 3:2" in corpo["prompt"]
    f = await rig_img.client.get(f"/v1/jobs/{jid}/file", headers=H)
    assert f.status_code == 200 and f.content == PNG and f.headers["content-type"] == "image/png"
    assert (await rig_img.client.get(f"/v1/jobs/{jid}", headers=H)).json()["state"] == "done"
    assert not [c for c in rig_img.up.calls if c[0] == "/v1/images/generations"]  # a GPU de midia nao e chamada


async def test_sincrono_devolve_b64(rig_img: Rig):
    r = await rig_img.client.post("/v1/images/generations", json={"prompt": "gato"}, headers=H)
    assert r.status_code == 200 and base64.b64decode(r.json()["data"][0]["b64_json"]) == PNG


async def test_falha_do_router(rig_img: Rig):
    rig_img.up.router_falha = True
    r = await rig_img.client.post("/v1/images/generations", json={"prompt": "gato", "async": True}, headers=H)
    s = await _esperar(rig_img, r.json()["id"])
    assert s["status"] == "failed" and "Nada foi cobrado" in s["message"]
    r = await rig_img.client.post("/v1/images/generations", json={"prompt": "gato"}, headers=H)
    assert r.status_code == 502


async def test_prompt_vazio_e_job_alheio(rig_img: Rig):
    assert (await rig_img.client.post("/v1/images/generations", json={"prompt": " "}, headers=H)).status_code == 422
    assert (await rig_img.client.get("/v1/media/jobs/img-nao-existe")).status_code == 401
