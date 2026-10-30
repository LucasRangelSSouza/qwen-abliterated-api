"""Aviso de espera no stream, 202 de X-Queue-Wait: 0 e log de ciclos (Spec 14 §3 e §5.3.1)."""
import hashlib
import json

import httpx

from app.config import Settings
from app.gpu.controller import GPUUnavailable
from app.main import create_app
from tests.test_api import ADMIN, BODY, CONSUMERS, H, StubCtl, make_upstream, rig, sse  # noqa: F401  (rig e fixture)

DATA_OK = [b'data: {"ok":true}\n\n', b"data: [DONE]\n\n"]


def _avisos(content: bytes) -> list[dict]:
    return [json.loads(p[6:]) for p in content.decode().split("\n\n") if p.startswith("data: {") and "x_queue" in p]


async def test_aviso_de_espera_no_stream_com_gpu_desligada(rig):
    client, ctl, st, _ = rig
    ctl.state, ctl.delay = "LOADING", 0.3
    st["handler"] = lambda req, s: httpx.Response(200, content=sse(*DATA_OK))
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    avisos = _avisos(r.content)
    assert len(avisos) >= 2
    d = avisos[0]["choices"][0]["delta"]
    assert "não é erro" in d["reasoning"] and d["reasoning_content"] == d["reasoning"] and "content" not in d  # nao suja a resposta
    xq = avisos[0]["x_queue"]
    assert xq["phase"] == "loading_model" and xq["eta_s"] == 240 and "2 a 4 minutos" in xq["message"]
    assert "pronto" in avisos[-1]["choices"][0]["delta"]["reasoning"]
    assert r.content.index(b"x_queue") < r.content.index(b'data: {"ok":true}')  # aviso antes do primeiro dado real


async def test_sem_aviso_com_gpu_quente_e_aviso_pode_virar_conteudo(rig):
    client, ctl, st, _ = rig
    ctl.delay = 0.1
    st["handler"] = lambda req, s: httpx.Response(200, content=sse(*DATA_OK))
    r = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
    assert b"x_queue" not in r.content
    ctl.state = "STARTING"
    r2 = await client.post("/v1/chat/completions", json={**BODY, "stream": True}, headers={**H, "X-Queue-Notice": "content"})
    first = json.loads(r2.content.decode().split("\n\n")[0][6:])
    assert "não é erro" in first["choices"][0]["delta"]["content"] and "reasoning" not in first["choices"][0]["delta"]


async def test_atualizacao_periodica_e_erro_nao_diz_pronto(redis):
    ctl = StubCtl()
    ctl.state, ctl.delay = "FALLBACK", 0.35
    ctl.error = GPUUnavailable("sem_oferta", "sem oferta", 300)
    s = Settings(tunnel_mode="none", keepalive_s=0.05, notice_every_s=0.1)
    app = create_app(s, controller=ctl, redis=redis, http=make_upstream(lambda req: httpx.Response(200, json={})), consumers=CONSUMERS)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await c.post("/v1/chat/completions", json={**BODY, "stream": True}, headers=H)
        text = r.content.decode()
        assert text.count('"x_queue"') >= 3 and "Ainda ligando" in text and "alugando" in text
        assert "Computador da IA pronto" not in text and "sem_oferta" in text and text.endswith("data: [DONE]\n\n")
        await c.aclose()


async def test_queue_wait_zero_devolve_202_e_acorda(rig):
    client, ctl, st, _ = rig
    ctl.state = "OFF"
    r = await client.post("/v1/chat/completions", json=BODY, headers={**H, "X-Queue-Wait": "0"})
    assert r.status_code == 202 and r.headers["retry-after"] == "240" and r.json()["status"] == "waking" and ctl.woken and st["calls"] == 0


async def test_ciclos_de_gpu_so_com_chave_de_admin(redis):
    s = Settings(tunnel_mode="none", admin_key_sha256=hashlib.sha256(ADMIN.encode()).hexdigest())
    app = create_app(s, controller=StubCtl(), redis=redis, http=make_upstream(lambda req: httpx.Response(200, json={})), consumers=CONSUMERS)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.get("/admin/gpu/cycles")).status_code == 403
        r = await c.get("/admin/gpu/cycles", headers={"X-Admin-Key": ADMIN})
        assert r.status_code == 200 and r.json()["cycles"][0]["id"] == 1
        await c.aclose()
