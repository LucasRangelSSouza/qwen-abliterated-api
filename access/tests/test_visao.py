"""Visao pelo ChatGPT (9Router global) com reserva no modelo da casa."""
import httpx
import pytest_asyncio

from tests.conftest import OWNER_KEY, Rig

H = {"Authorization": f"Bearer {OWNER_KEY}"}
IMG = {"model": "visao", "messages": [{"role": "user", "content": [{"type": "text", "text": "o que tem aqui?"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}


@pytest_asyncio.fixture
async def rig_v(tmp_path):
    r = Rig(tmp_path, image_router_url="http://up/router", image_router_key="r9-chave")
    await r.start()
    r.up.vis = []
    r.up.falha = False
    r.up.casa = []

    async def handler(request: httpx.Request, body):
        if request.url.path == "/router/v1/chat/completions":
            r.up.vis.append(body)
            if r.up.falha:
                return httpx.Response(502, json={"error": {"message": "fora"}})
            return httpx.Response(200, json={"id": "x", "choices": [{"message": {"role": "assistant", "content": "um gato azul"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
        if request.url.path.endswith("/chat/completions"):
            r.up.casa.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "resposta da casa"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


async def test_visao_vai_ao_chatgpt(rig_v: Rig):
    r = await rig_v.client.post("/v1/chat/completions", json=IMG, headers=H)
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "um gato azul"
    assert rig_v.up.vis[0]["model"] == "cx/gpt-5.6-luna" and not rig_v.up.casa


async def test_visao_em_stream(rig_v: Rig):
    r = await rig_v.client.post("/v1/chat/completions", json={**IMG, "stream": True}, headers=H)
    assert r.status_code == 200 and "um gato azul" in r.text and "[DONE]" in r.text


async def test_chatgpt_fora_cai_no_modelo_da_casa(rig_v: Rig):
    rig_v.up.falha = True
    r = await rig_v.client.post("/v1/chat/completions", json=IMG, headers=H)
    assert r.status_code == 200
    assert rig_v.up.casa and rig_v.up.casa[0]["model"] == "qwen-abliterated"
