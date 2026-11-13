"""Credito unico do Estudio: 1 credito = US$ 0,02, sempre multiplos de 5, creditos gratis do plano por mes, compra pelo webhook, cobranca antes e devolucao em falha."""
import json

import httpx
import pytest

from app import credits as C
from tests.conftest import Rig
from tests.test_credits import SEG, assinante, webhook


@pytest.fixture
async def rig3(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG, runware_api_key="rw", runware_url="http://up/runware")
    r.up.rw = []
    r.up.falha = False

    async def handler(request: httpx.Request, body):
        if request.url.path == "/runware":
            r.up.rw.append(body)
            if r.up.falha:
                return httpx.Response(400, json={"errors": [{"message": "model not found"}]})
            t = body[0]
            if t["taskType"] == "mediaStorage":
                return httpx.Response(200, json={"data": [{"mediaUUID": "m"}]})
            return httpx.Response(200, json={"data": [{"taskUUID": t["taskUUID"], "status": "processing"}]})
        return httpx.Response(404, json={})
    r.up.handler = handler
    return r


def test_arredondamento_em_multiplos_de_5():
    assert C.studio_creditos(0.01) == 5
    assert C.studio_creditos(2.50) == 125
    assert C.studio_creditos(2.51) == 130
    assert C.studio_creditos(3.6364) == 185  # teto de R$ 20 a 5,50


async def test_plano_ganha_creditos_gratis_uma_vez_por_mes(rig3: Rig):
    ctx = rig3.app.state.ctx
    pid, key, _ = await assinante(rig3, "light")
    now = rig3.clock()
    assert C.studio_balance(ctx.store, pid, now) == 100
    assert C.studio_balance(ctx.store, pid, now + 3600) == 100  # nao duplica no mesmo mes
    assert C.studio_balance(ctx.store, pid, now + 40 * 86400) == 100  # mes seguinte: pacote novo; o antigo venceu
    assert ctx.store.one("SELECT COUNT(*) n FROM credits WHERE person_id=? AND source='plan'", (pid,))["n"] == 2


async def test_orcar_mostra_creditos_e_saldo(rig3: Rig):
    pid, key, _ = await assinante(rig3, "max")
    h = {"Authorization": f"Bearer {key}"}
    o = (await rig3.client.post("/v1/estudio/orcar", json={"efeito": "trocar-personagem", "segundos": 10}, headers=h)).json()
    assert o["preco_usd"] == 2.5 and o["creditos"] == 125 and o["saldo"] == 300


async def test_gerar_debita_creditos_e_devolve_se_a_runware_recusa(rig3: Rig):
    ctx = rig3.app.state.ctx
    pid, key, _ = await assinante(rig3, "max")
    h = {"Authorization": f"Bearer {key}"}
    corpo = {"efeito": "trocar-personagem", "segundos": 10, "inputs": {"video": "https://ex/v.mp4", "referenceImages": ["https://ex/f.jpg"]}}
    r = await rig3.client.post("/v1/estudio/gerar", json=corpo, headers=h)
    assert r.status_code == 200 and r.json()["creditos"] == 125
    assert C.studio_balance(ctx.store, pid, rig3.clock()) == 300 - 125
    rig3.up.falha = True
    r = await rig3.client.post("/v1/estudio/gerar", json=corpo, headers=h)
    assert r.status_code == 422
    assert C.studio_balance(ctx.store, pid, rig3.clock()) == 300 - 125, "falha do fornecedor nao cobra"


async def test_sem_saldo_responde_402_com_o_que_falta(rig3: Rig):
    pid, key, _ = await assinante(rig3, "light")  # 100 gratis; o pedido custa 125
    h = {"Authorization": f"Bearer {key}"}
    r = await rig3.client.post("/v1/estudio/gerar", json={"efeito": "trocar-personagem", "segundos": 10}, headers=h)
    assert r.status_code == 402
    e = r.json()["error"]
    assert e["code"] == "media_quota_exceeded" and e["kind"] == "studio" and e["needed"] == 125 and e["balance"] == 100
    assert not rig3.up.rw, "nada foi mandado ao fornecedor"


async def test_compra_pelo_webhook_soma_creditos_do_estudio(rig3: Rig):
    ctx = rig3.app.state.ctx
    pid, key, email = await assinante(rig3, "light")
    r = await webhook(rig3, "Segundo Cérebro - Créditos do Estúdio", email)
    assert r.status_code == 200, r.text
    assert C.studio_balance(ctx.store, pid, rig3.clock()) == 100 + 900
    j = (await rig3.client.get("/v1/credits", headers={"Authorization": f"Bearer {key}"})).json()
    assert j["balances"]["studio"] == 1000
    assert [p for p in j["packs"] if p["kind"] == "studio"][0]["units"] == 900


async def test_pedido_antigo_de_troca_de_personagem_tambem_cobra_em_creditos(make_rig):
    import base64
    rig = await make_rig(runware_api_key="rw", runware_url="http://up/runware", media_backend="runware")
    ctx = rig.app.state.ctx
    pid, key, _ = await assinante(rig, "max")

    async def handler(request, body):
        if request.url.path == "/runware":
            t = body[0]
            return httpx.Response(200, json={"data": [{"mediaUUID": "m"} if t["taskType"] == "mediaStorage" else {"taskUUID": t["taskUUID"], "status": "processing"}]})
        return httpx.Response(404, json={})
    rig.up.handler = handler
    r = await rig.client.post("/v1/videos/generations", json={"recipe": "openjutsu-swap", "video": base64.b64encode(b"x" * 600).decode(), "image": base64.b64encode(b"y" * 600).decode(), "async": True},
                              headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202, r.text
    assert C.studio_balance(ctx.store, pid, rig.clock()) < 300
