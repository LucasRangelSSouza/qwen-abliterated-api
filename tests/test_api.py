import asyncio
import hashlib
import json

import httpx
import pytest
import pytest_asyncio

from app.config import Settings
from app.gpu.controller import GPUUnavailable
from app.main import create_app

KEY = "sk-teste-123"
CONSUMERS = {hashlib.sha256(KEY.encode()).hexdigest(): {"name": "luar", "priority": False}}
H = {"Authorization": f"Bearer {KEY}"}
BODY = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": "oi"}], "max_tokens": 8}


class _Ledger:
    async def snapshot(self):
        return {"month_usd": 1.5, "day_usd": 0.2}


class StubCtl:
    """Controlador falso: ensure_ready com atraso/erro configuravel."""
    def __init__(self):
        self.state = "READY"
        self.delay = 0.0
        self.error: GPUUnavailable | None = None
        self.begun = self.ended = 0
        self.ledger = _Ledger()

    async def ensure_ready(self, priority=False, timeout=None):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error

    def begin_request(self): self.begun += 1
    def end_request(self): self.ended += 1
    def touch(self): pass
    def status(self): return {"state": self.state, "eta_s": None}
    def start(self): pass
    async def stop_loop(self): pass


def sse(*parts):
    async def gen():
        for p in parts:
            yield p
            await asyncio.sleep(0)
    return gen()


def make_upstream(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://up")


@pytest_asyncio.fixture
async def rig(redis):
    state = {"calls": 0, "handler": None}

    def h(request: httpx.Request):
        state["calls"] += 1
        return state["handler"](request, state)

    ctl = StubCtl()
    s = Settings(tunnel_mode="none", keepalive_s=0.05, embed_mode="gpu", chat_queue_max=1)
    app = create_app(s, controller=ctl, redis=redis, http=make_upstream(h), consumers=CONSUMERS)
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        yield client, ctl, state, app
        await client.aclose()


async def test_auth_e_modelos(rig):
    client, *_ = rig
    assert (await client.get("/v1/models")).status_code == 401
    r = await client.get("/v1/models", headers=H)
    assert r.status_code == 200 and {m["id"] for m in r.json()["data"]} == {"qwen-abliterated", "qwen-embedding"}
    assert (await client.get("/health")).json() == {"ok": True}


async def test_chat_json_passa_direto(rig):
    client, ctl, st, _ = rig
    st["handler"] = lambda req, s: httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": "ola"}}]})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "ola"
    assert r.headers["x-llm-state"] == "READY" and ctl.begun == 1 and ctl.ended == 1


async def test_chat_stream_passa_chunks(rig):
    client, _, st, _ = rig
    st["handler"] = lambda req, s: httpx.Response(200, content=sse(b'data: {"a":1}\n\n', b'data: {"a":2}\n\n', b"data: [DONE]\n\n"))
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.content == b'data: {"a":1}\n\ndata: {"a":2}\n\ndata: [DONE]\n\n'


async def test_keepalive_sse_durante_cold_start(rig):
    client, ctl, st, _ = rig
    ctl.delay = 0.3
    st["handler"] = lambda req, s: httpx.Response(200, content=sse(b'data: {"ok":true}\n\n', b"data: [DONE]\n\n"))
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    assert r.content.count(b": acordando\n\n") >= 3  # comentarios SSE antes do primeiro dado
    assert r.content.endswith(b'data: {"ok":true}\n\ndata: [DONE]\n\n')
    assert r.content.index(b": acordando") < r.content.index(b"data:")


async def test_keepalive_json_com_espacos_e_corpo_valido(rig):
    client, ctl, st, _ = rig
    ctl.delay = 0.3
    st["handler"] = lambda req, s: httpx.Response(200, json={"id": "ok"})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.content.startswith(b" ") and json.loads(r.content) == {"id": "ok"}  # JSON valido apesar dos espacos


async def test_gpu_indisponivel_vira_erro_openai_no_corpo(rig):
    client, ctl, st, _ = rig
    ctl.error = GPUUnavailable("sem_oferta", "sem oferta", 300)
    st["handler"] = lambda req, s: httpx.Response(200, json={})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    j = json.loads(r.content)
    assert j["error"]["code"] == "sem_oferta" and "300" in j["error"]["message"] and st["calls"] == 0
    rs = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    assert b"sem_oferta" in rs.content and rs.content.endswith(b"data: [DONE]\n\n")


async def test_fila_cheia_devolve_429(rig):
    client, ctl, st, _ = rig
    ctl.delay = 0.4
    st["handler"] = lambda req, s: httpx.Response(200, json={"id": "ok"})
    t1 = asyncio.create_task(client.post("/v1/chat/completions", json=BODY, headers=H))
    await asyncio.sleep(0.1)  # a primeira esta esperando a GPU (1 na fila = teto de chat_queue_max)
    r2 = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r2.status_code == 429 and r2.headers["retry-after"]
    assert (await t1).status_code == 200


async def test_batch_desligado_e_classe_invalida(rig):
    client, *_ = rig
    r = await client.post("/v1/chat/completions", json=BODY, headers={**H, "X-Queue-Class": "batch"})
    assert r.status_code == 403
    r = await client.post("/v1/chat/completions", json=BODY, headers={**H, "X-Queue-Class": "xyz"})
    assert r.status_code == 400


async def test_embeddings_passa_e_exige_modo_gpu(rig, redis):
    client, ctl, st, _ = rig
    st["handler"] = lambda req, s: httpx.Response(200, json={"data": [{"embedding": [0.1] * 768, "index": 0}], "usage": {"prompt_tokens": 3}})
    r = await client.post("/v1/embeddings", json={"model": "qwen-embedding", "input": "oi", "dimensions": 768}, headers=H)
    assert r.status_code == 200 and len(r.json()["data"][0]["embedding"]) == 768
    app_off = create_app(Settings(tunnel_mode="none", embed_mode="off"), controller=StubCtl(), redis=redis,
                         http=make_upstream(lambda req: httpx.Response(200, json={})), consumers=CONSUMERS)
    async with app_off.router.lifespan_context(app_off):
        c2 = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_off), base_url="http://t")
        r2 = await c2.post("/v1/embeddings", json={"model": "qwen-embedding", "input": "oi"}, headers=H)
        assert r2.status_code == 503 and r2.json()["detail"]["error"]["code"] == "embeddings_disabled"
        await c2.aclose()


async def test_refaz_uma_vez_se_o_upstream_cai_antes_do_primeiro_byte(rig):
    client, _, st, _ = rig

    def handler(req, s):
        if s["calls"] == 1:
            raise httpx.ConnectError("caiu", request=req)
        return httpx.Response(200, json={"id": "depois"})
    st["handler"] = handler
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert json.loads(r.content) == {"id": "depois"} and st["calls"] == 2


async def test_upstream_cai_depois_de_enviar_bytes_vira_erro_no_stream(rig):
    client, _, st, _ = rig

    async def gen():
        yield b'data: {"a":1}\n\n'
        raise httpx.ReadError("cortou")

    st["handler"] = lambda req, s: httpx.Response(200, content=gen())
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    assert b'data: {"a":1}' in r.content and b"upstream_error" in r.content and r.content.endswith(b"data: [DONE]\n\n")


async def test_erro_do_vllm_e_repassado(rig):
    client, _, st, _ = rig
    st["handler"] = lambda req, s: httpx.Response(400, json={"error": {"message": "max_tokens too large"}})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert json.loads(r.content)["error"]["message"] == "max_tokens too large"


async def test_status_e_metricas(rig):
    client, *_ = rig
    r = await client.get("/v1/gpu/status", headers=H)
    j = r.json()
    assert j["state"] == "READY" and j["spend"]["month_usd"] == 1.5 and j["spend"]["month_cap_usd"] == 80.0 and "chat" in j["queue"]
    m = await client.get("/metrics")
    assert m.status_code == 200 and b"llmq_gpu_state" in m.content
