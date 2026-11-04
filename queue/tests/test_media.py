"""Fila de imagem: ComfyUI falso (httpx.MockTransport), Vast e Redis falsos. Nada gasta GPU nem dinheiro."""
import asyncio
import hashlib

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Settings
from app.gpu.tunnel import NoTunnel
from app.gpu.vast import Instance
from app.main import create_app
from app.media import Media, MediaError, _size
from app.scheduler import QueueFull

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class FakeComfy:
    """Responde como o ComfyUI: /prompt devolve um id, /history so tem a entrada depois de `delay` consultas, /view devolve a imagem."""
    def __init__(self, delay: int = 1, error: str | None = None, down: bool = False):
        self.delay, self.error, self.down = delay, error, down
        self.prompts: list[dict] = []
        self.polls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("sem conexao")
        path = req.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.test"}, "devices": [{"name": "RTX 3090", "vram_total": 24, "vram_free": 20}]})
        if path == "/prompt":
            import json
            self.prompts.append(json.loads(req.content))
            return httpx.Response(200, json={"prompt_id": f"p{len(self.prompts)}"})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            self.polls += 1
            if self.polls <= self.delay:
                return httpx.Response(200, json={})
            if self.error:
                return httpx.Response(200, json={pid: {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": self.error}]]}}})
            return httpx.Response(200, json={pid: {"status": {"status_str": "success", "completed": True}, "outputs": {"7": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if path == "/view":
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)


class FakeVastMedia:
    def __init__(self, status="running"):
        self.status = status

    async def get_instance(self, iid):
        return Instance(id=iid, actual_status=self.status, intended_status="running", status_msg=None, ssh_host="h.vast.ai", ssh_port=2222,
                        dph_total=0.128, storage_total_cost=0.02, machine_id=1, raw={"gpu_name": "RTX 3090", "num_gpus": 1, "geolocation": "Brazil, BR", "label": "midia-gpu-1"})


def settings(**kw) -> Settings:
    base = dict(tunnel_mode="none", media_instance_id=77, media_poll_s=0.01, media_job_timeout_s=2.0, media_sync_wait_s=5.0, media_queue_max=3, media_per_consumer=2)
    base.update(kw)
    return Settings(**base)


@pytest_asyncio.fixture
async def make_media(redis):
    made = []

    async def _make(comfy: FakeComfy | None = None, vast=None, **kw):
        comfy = comfy or FakeComfy()
        client = httpx.AsyncClient(transport=httpx.MockTransport(comfy.handler))
        m = Media(settings(**kw), vast or FakeVastMedia(), NoTunnel(), redis, client)
        made.append(client)
        return m, comfy

    yield _make
    for c in made:
        await c.aclose()


def test_size_arredonda_e_limita():
    assert _size({"size": "1024x1024"}) == (1024, 1024)
    assert _size({"size": "1000x700"}) == (1024, 704)
    w, h = _size({"size": "4000x4000"})
    assert w * h <= 1_650_000 and w % 64 == 0 and h % 64 == 0
    assert _size({"width": 10, "height": 10}) == (256, 256)
    assert _size({"size": "lixo"}) == (1024, 1024)


@pytest.mark.asyncio
async def test_gera_imagem_e_guarda_o_historico(make_media):
    m, comfy = await make_media()
    job = await m.submit("portfolio", "Lucas", {"prompt": "um hamburguer", "size": "1024x768", "seed": 5})
    final = await m.wait(job["id"], 5)
    assert final["state"] == "done" and final["seconds"] is not None
    assert await m.image(job["id"]) == PNG
    wf = comfy.prompts[0]["prompt"]
    assert wf["2"]["inputs"]["text"] == "um hamburguer"
    assert (wf["4"]["inputs"]["width"], wf["4"]["inputs"]["height"]) == (1024, 768) and wf["5"]["inputs"]["seed"] == 5
    rows = (await m.jobs())["recent"]
    assert rows[0]["id"] == job["id"] and rows[0]["end_user"] == "Lucas" and rows[0]["prompt"] == "um hamburguer"


@pytest.mark.asyncio
async def test_uma_por_vez_e_fila_cheia(make_media):
    m, comfy = await make_media(FakeComfy(delay=30), media_queue_max=2)
    a = await m.submit("a", "", {"prompt": "1"})
    await asyncio.sleep(0.05)
    b = await m.submit("b", "", {"prompt": "2"})
    c = await m.submit("c", "", {"prompt": "3"})
    st = await m.jobs()
    assert [j["id"] for j in st["running"]] == [a["id"]]
    assert {j["id"] for j in st["waiting"]} == {b["id"], c["id"]}
    with pytest.raises(QueueFull):
        await m.submit("d", "", {"prompt": "4"})
    for t in list(m.tasks.values()):
        t.cancel()
    await asyncio.gather(*m.tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_erro_do_comfy_vira_falha_legivel(make_media):
    m, _ = await make_media(FakeComfy(error="Out of memory"))
    job = await m.submit("a", "", {"prompt": "x"})
    final = await m.wait(job["id"], 5)
    assert final["state"] == "failed" and "Out of memory" in final["error"]
    # a falha nao trava a fila: a pista fica livre
    assert m.lane.stats()["inflight"] == 0


@pytest.mark.asyncio
async def test_maquina_fora_do_ar(make_media):
    class TunDown(NoTunnel):
        async def healthy(self):
            return False
    m, _ = await make_media(vast=FakeVastMedia(status="exited"))
    m.tun = TunDown()
    job = await m.submit("a", "", {"prompt": "x"})
    final = await m.wait(job["id"], 5)
    assert final["state"] == "failed" and final["error"].startswith("maquina de imagem indisponivel")


@pytest.mark.asyncio
async def test_tempo_esgotado(make_media):
    m, _ = await make_media(FakeComfy(delay=10**6), media_job_timeout_s=0.1)
    job = await m.submit("a", "", {"prompt": "x"})
    final = await m.wait(job["id"], 5)
    assert final["state"] == "failed" and "tempo esgotado" in final["error"]


@pytest.mark.asyncio
async def test_pedido_invalido(make_media):
    m, _ = await make_media()
    with pytest.raises(MediaError):
        await m.submit("a", "", {"prompt": "   "})
    with pytest.raises(MediaError):
        await m.submit("a", "", {"prompt": "x", "recipe": "nao-existe"})


@pytest.mark.asyncio
async def test_status_da_maquina(make_media):
    m, _ = await make_media()
    st = await m.status()
    assert st["state"] == "ready" and st["instance"]["dph"] == 0.128 and st["comfy"]["gpu"] == "RTX 3090"
    assert st["monthly_cost_usd"] == pytest.approx(92.16)


# ---- HTTP (rotas publicas e de admin)

ADMIN = "adm-key"


def _client(redis, comfy: FakeComfy):
    key = "k-teste"
    s = settings(admin_key_sha256=hashlib.sha256(ADMIN.encode()).hexdigest())
    http = httpx.AsyncClient(transport=httpx.MockTransport(comfy.handler))
    media = Media(s, FakeVastMedia(), NoTunnel(), redis, http)
    consumers = {hashlib.sha256(key.encode()).hexdigest(): {"name": "portfolio", "key_sha256": hashlib.sha256(key.encode()).hexdigest(), "priority": 0}}
    from tests.test_api import StubCtl  # controlador falso ja usado nos testes de API
    return create_app(s, controller=StubCtl(), redis=redis, http=http, consumers=consumers, media=media), key


def test_rotas_http(redis):
    app, key = _client(redis, FakeComfy())
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {key}"}
        r = c.post("/v1/images/generations", json={"prompt": "hamburguer", "size": "512x512"}, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["data"][0]["b64_json"] and j["size"] == "512x512"
        jid = j["id"]
        assert c.get(f"/v1/jobs/{jid}", headers=h).json()["state"] == "done"
        assert c.get(f"/v1/jobs/{jid}/image", headers=h).content == PNG
        assert c.post("/v1/images/generations", json={"prompt": "x"}).status_code == 401
        assert c.post("/v1/images/generations", json={"prompt": ""}, headers=h).status_code == 422
        # admin
        assert c.get("/admin/media/status").status_code == 403
        ad = {"X-Admin-Key": ADMIN}
        assert c.get("/admin/media/status", headers=ad).json()["state"] == "ready"
        jobs = c.get("/admin/media/jobs", headers=ad).json()
        assert jobs["enabled"] and jobs["recent"][0]["id"] == jid
        # async
        r2 = c.post("/v1/images/generations", json={"prompt": "x", "async": True}, headers=h)
        assert r2.status_code == 202 and r2.json()["poll"].startswith("/v1/jobs/")


@pytest.mark.asyncio
async def test_vigia_reabre_o_tunel_sozinho(make_media):
    class Tun(NoTunnel):
        def __init__(self):
            super().__init__()
            self.ok = False

        async def healthy(self):
            return self.ok

        async def open(self, host, port):
            self.opened.append((host, port))
            self.ok = True
            return True
    m, _ = await make_media(media_keepalive_s=0.02)
    m.tun = Tun()
    assert (await m.status())["tunnel"] is False
    m.start()
    await asyncio.sleep(0.2)
    assert m.tun.opened == [("h.vast.ai", 2222)] and (await m.status())["tunnel"] is True
    m.tun.ok = False                      # o tunel cai...
    await asyncio.sleep(0.2)
    assert len(m.tun.opened) == 2         # ...e o vigia abre de novo
    await m.stop()


@pytest.mark.asyncio
async def test_vigia_nao_abre_com_a_maquina_parada(make_media):
    class Tun(NoTunnel):
        async def healthy(self):
            return False
    m, _ = await make_media(vast=FakeVastMedia(status="exited"), media_keepalive_s=0.02)
    m.tun = Tun()
    m.start()
    await asyncio.sleep(0.15)
    assert m.tun.opened == []
    await m.stop()
