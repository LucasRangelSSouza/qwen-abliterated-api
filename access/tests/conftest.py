"""Equipamento de teste: upstream falso (httpx.MockTransport), relogio injetavel e SQLite em arquivo temporario. Sem rede, sem GPU."""
import asyncio
import hashlib
import json

import httpx
import pytest_asyncio

from app.config import Settings
from app.main import create_app

UP_KEY = "sk-queue-teste"
UP_ADMIN = "admin-queue-teste"
PASSWORD = "senha-do-admin-123"
OWNER_KEY = "sk-dono-teste"
PLATFORM_KEY = "sk-luar-teste"
BODY = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": "oi"}], "max_tokens": 20}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def chunk(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def content_chunk(text: str) -> bytes:
    return chunk({"choices": [{"index": 0, "delta": {"content": text}}]})


def usage_chunk(total: int, prompt: int = 10) -> bytes:
    return chunk({"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": total - prompt, "total_tokens": total}})


class Upstream:
    """Estado do queue falso. `total` e o consumo que ele reporta por chamada; `handler` substitui o comportamento padrao."""

    def __init__(self):
        self.calls = []        # (path, body, headers) de cada chamada de IA
        self.total = 30
        self.delay = 0.0
        self.handler = None
        self.cycles = [{"id": "c1", "kind": "cold", "started_at": "2026-10-30T10:00:00Z", "cold_start_s": 400.0, "cost_usd": 0.12}]
        self.fail_machines = False

    @property
    def ai_calls(self) -> int:
        return len(self.calls)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/gpu/status":
            if self.fail_machines:
                return httpx.Response(500, json={"error": "x"})
            return httpx.Response(200, json={"state": "READY", "eta_s": None, "queue": {},
                                             "spend": {"month_usd": 1.0, "day_usd": 0.1, "month_cap_usd": 80.0, "remaining_usd": 79.0}})
        if path == "/admin/gpu/cycles":
            if self.fail_machines or request.headers.get("x-admin-key") != UP_ADMIN:
                return httpx.Response(403, json={"error": "x"})
            return httpx.Response(200, json={"cycles": self.cycles})
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen-abliterated"}]})
        body = json.loads(request.content)
        self.calls.append((path, body, dict(request.headers)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.handler:
            return await self.handler(request, body)
        if path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 7, "total_tokens": 7}})
        if body.get("stream"):
            return sse_response([content_chunk("ola"), content_chunk(" mundo"), usage_chunk(self.total), b"data: [DONE]\n\n"])
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ola mundo"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": self.total - 10, "total_tokens": self.total}})


def sse_response(parts, error=None, headers=None) -> httpx.Response:
    """Stream SSE; se `error`, levanta no fim (conexao do upstream cai no meio)."""
    async def gen():
        for p in parts:
            yield p
            await asyncio.sleep(0)
        if error:
            raise error
    return httpx.Response(200, headers={"content-type": "text/event-stream", **(headers or {})}, content=gen())


class Rig:
    def __init__(self, tmp_path, **kw):
        self.db = str(tmp_path / "access.db")
        self.clock = Clock()
        self.up = Upstream()
        self.kw = dict(db_path=self.db, upstream_url="http://up", upstream_key=UP_KEY, upstream_admin_key=UP_ADMIN,
                       admin_password=PASSWORD, session_secret="segredo-de-teste", owner_key_sha256=sha(OWNER_KEY),
                       platform_key_sha256=sha(PLATFORM_KEY), panel_dir=str(tmp_path / "sem-painel"))
        self.kw.update(kw)
        self.app = self.client = self._cm = None

    async def start(self):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.up), base_url="http://up")
        self.app = create_app(Settings(**self.kw), http=http, clock=self.clock)
        self._cm = self.app.router.lifespan_context(self.app)
        await self._cm.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")

    async def stop(self):
        await self.client.aclose()
        await self._cm.__aexit__(None, None, None)

    async def restart(self):
        await self.stop()
        await self.start()

    async def login(self):
        r = await self.client.post("/admin/api/login", json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        return r

    async def person(self, name="ana", **limits):
        """Cria pessoa pelo painel; devolve (id, chave)."""
        if not self.client.cookies:
            await self.login()
        r = await self.client.post("/admin/api/people", json={"name": name, **limits})
        assert r.status_code == 200, r.text
        return r.json()["person"]["id"], r.json()["api_key"]

    async def chat(self, key, body=None, **headers):
        return await self.client.post("/v1/chat/completions", json=body or BODY, headers={"Authorization": f"Bearer {key}", **headers})

    async def usage(self, key):
        r = await self.client.get("/v1/usage", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        return r.json()


@pytest_asyncio.fixture
async def rig(tmp_path):
    r = Rig(tmp_path)
    await r.start()
    yield r
    await r.stop()


@pytest_asyncio.fixture
async def make_rig(tmp_path):
    """Fabrica para testes que precisam de Settings diferentes (modo QA, banco quebrado, painel)."""
    made = []

    async def f(**kw):
        r = Rig(tmp_path, **kw)
        await r.start()
        made.append(r)
        return r
    yield f
    for r in made:
        await r.stop()
