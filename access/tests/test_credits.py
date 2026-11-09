"""Creditos avulsos de R$ 100 (Spec 24): consumo depois que o plano estoura, validade, devolucao em falha, compra pelo webhook, rota do cliente e painel."""
import json

import httpx
import pytest

from app import accounts as A
from app import credits as C
from app.ledger import Blocked
from tests.conftest import BODY, Rig

SEG = "seg-cakto-teste"
DAY = 86400


async def assinante(rig: Rig, plano: str):
    """Pessoa com conta e assinatura ativa no plano; devolve (pessoa, chave, e-mail)."""
    pid, key = await rig.person(f"cli-{plano}")
    with rig.app.state.ctx.store.tx() as c:
        aid = c.execute("INSERT INTO accounts(email,name,person_id,created_at) VALUES(?,?,?,?)", (f"{plano}@x.com", plano, pid, 1.0)).lastrowid
        c.execute("INSERT INTO subscriptions(account_id,plan,status,updated_at) VALUES(?,?,?,?)", (aid, plano, "active", 1.0))
    A.ensure_plans(rig.app.state.ctx)
    return pid, key, f"{plano}@x.com"


async def webhook(rig: Rig, produto, email, evento="purchase_approved", pedido="ord-1", transacao=None):
    corpo = {"event": evento, "data": {"id": pedido, "transaction_id": transacao or f"{pedido}-{evento}", "customer": {"name": "Ana", "email": email}, "product": {"name": produto}, "amount": 100.0}}
    return await rig.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers={"content-type": "application/json", "x-webhook-secret": SEG})


@pytest.fixture
async def rig2(make_rig):
    return await make_rig(cakto_webhook_secret=SEG)


# ---------------------------------------------------------------- consumo de tokens

async def test_plano_estourado_passa_pelo_credito_e_nao_conta_nas_janelas(rig2):
    rig = rig2
    pid, _key, _ = await assinante(rig, "light")  # Lite: 1M por sessao
    ctx = rig.app.state.ctx
    ctx.ledger.commit(ctx.ledger.reserve(pid, 1_000_000), 1_000_000)  # sessao toda gasta
    with pytest.raises(Blocked):
        ctx.ledger.reserve(pid, 500)
    C.grant(ctx.store, pid, "tokens", 2_000_000, ctx.clock())
    res = ctx.ledger.reserve(pid, 500)
    assert res.credit_parts and ctx.ledger.windows(pid)["session"]["used"] == 1_000_000  # janela intacta
    ctx.ledger.commit(res, 120)  # gastou 120 de 500: devolve 380
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 2_000_000 - 120
    assert ctx.ledger.windows(pid)["session"]["used"] == 1_000_000


async def test_pedido_que_falha_devolve_o_credito_inteiro(rig2):
    rig = rig2
    pid, _k, _ = await assinante(rig, "light")
    ctx = rig.app.state.ctx
    ctx.ledger.commit(ctx.ledger.reserve(pid, 1_000_000), 1_000_000)
    C.grant(ctx.store, pid, "tokens", 1000, ctx.clock())
    ctx.ledger.release(ctx.ledger.reserve(pid, 700))
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 1000


async def test_credito_sem_saldo_suficiente_continua_bloqueado_e_nao_gasta_nada(rig2):
    rig = rig2
    pid, _k, _ = await assinante(rig, "light")
    ctx = rig.app.state.ctx
    ctx.ledger.commit(ctx.ledger.reserve(pid, 1_000_000), 1_000_000)
    C.grant(ctx.store, pid, "tokens", 300, ctx.clock())
    with pytest.raises(Blocked):
        ctx.ledger.reserve(pid, 500)
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 300


async def test_credito_vence_e_o_que_vence_primeiro_e_gasto_primeiro(rig2):
    rig = rig2
    pid, _k, _ = await assinante(rig, "light")
    ctx = rig.app.state.ctx
    a = C.grant(ctx.store, pid, "tokens", 100, ctx.clock())
    rig.clock.advance(DAY)
    b = C.grant(ctx.store, pid, "tokens", 100, ctx.clock())  # vence 1 dia depois do primeiro
    with ctx.store.tx() as c:
        parts = C.take(c, pid, "tokens", 150, ctx.clock())
    assert parts == [(a, 100), (b, 50)]  # o mais velho acaba antes
    rig.clock.advance(31 * DAY)  # os dois passam da validade de 30 dias
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 0


# ---------------------------------------------------------------- HTTP: chat e midia

async def test_chat_estourado_mostra_tipo_e_passa_apos_o_credito_do_painel(rig2):
    rig = rig2
    pid, key, _ = await assinante(rig, "light")
    await rig.login()
    assert (await rig.client.patch(f"/admin/api/people/{pid}", json={"session_limit_tokens": 5})).status_code == 200  # limite minusculo: qualquer pedido estoura
    h = {"Authorization": f"Bearer {key}"}
    r = await rig.client.post("/v1/chat/completions", json=BODY, headers=h)
    e = r.json()["error"]
    assert r.status_code == 429 and e["code"] == "usage_limit_reached" and e["kind"] == "tokens" and e["credits_available"] is False
    await rig.client.put("/admin/api/billing/settings", json={"credit_offers": {"tokens": "abc123"}})
    assert (await rig.client.post("/v1/chat/completions", json=BODY, headers=h)).json()["error"]["credits_available"] is True
    g = await rig.client.post(f"/admin/api/people/{pid}/credits", json={"kind": "tokens", "units": 100000})
    assert g.status_code == 200
    assert (await rig.client.post("/v1/chat/completions", json=BODY, headers=h)).status_code == 200
    saldo = (await rig.client.get(f"/admin/api/people/{pid}/credits")).json()["balances"]["tokens"]
    assert 0 < saldo < 100000  # gastou so o que o pedido usou


async def test_cota_de_video_estourada_usa_credito_inclusive_dividindo_o_pedido(rig2):
    rig = rig2
    pid, key, _ = await assinante(rig, "light")  # Lite: 5 unidades de video
    ctx = rig.app.state.ctx
    h = {"Authorization": f"Bearer {key}"}
    padrao = {"prompt": "x", "recipe": "wan22-a14b", "quality": "padrao"}
    for _ in range(4):
        assert (await rig.client.post("/v1/videos/generations", json=padrao, headers=h)).status_code == 200
    alta = {"prompt": "x", "recipe": "wan22-a14b", "quality": "alta"}  # 3 unidades, so 1 sobra no plano
    r = await rig.client.post("/v1/videos/generations", json=alta, headers=h)
    assert r.status_code == 429 and r.json()["error"]["kind"] == "video"
    C.grant(ctx.store, pid, "video", 10, ctx.clock())
    assert (await rig.client.post("/v1/videos/generations", json=alta, headers=h)).status_code == 200
    assert C.balances(ctx.store, pid, ctx.clock())["video"] == 8  # 1 veio do plano, 2 do credito

    async def recusa(request, body):
        return httpx.Response(500, json={"error": "x"})
    rig.up.handler = recusa  # pedido recusado pelo upstream devolve plano e credito
    assert (await rig.client.post("/v1/videos/generations", json=alta, headers=h)).status_code == 500
    assert C.balances(ctx.store, pid, ctx.clock())["video"] == 8


# ---------------------------------------------------------------- compra pelo webhook

async def test_compra_de_credito_de_uso_concede_o_dobro_da_sessao_sem_mexer_no_plano(rig2):
    rig = rig2
    pid, _k, email = await assinante(rig, "max")  # Max: 5M por sessao
    ctx = rig.app.state.ctx
    assert (await webhook(rig, "Segundo Cérebro - Crédito de uso", email)).status_code == 200
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 10_000_000
    assert ctx.store.one("SELECT COUNT(*) n FROM subscriptions")["n"] == 1 and ctx.store.one("SELECT plan FROM subscriptions")["plan"] == "max"
    assert ctx.store.one("SELECT COUNT(*) n FROM vms")["n"] == 0
    # webhook repetido (outro id de evento, mesmo pedido) nao concede de novo
    assert (await webhook(rig, "Segundo Cérebro - Crédito de uso", email, transacao="outro")).status_code == 200
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 10_000_000
    # reembolso zera o saldo daquele pedido
    assert (await webhook(rig, "Segundo Cérebro - Crédito de uso", email, evento="refund")).status_code == 200
    assert C.balances(ctx.store, pid, ctx.clock())["tokens"] == 0


async def test_compra_de_imagem_video_e_audio_usa_o_tamanho_configurado(rig2):
    rig = rig2
    pid, _k, email = await assinante(rig, "light")
    ctx = rig.app.state.ctx
    for i, nome in enumerate(("Segundo Cérebro - Crédito de imagens", "Segundo Cérebro - Crédito de vídeos", "Segundo Cérebro - Crédito de áudio")):
        assert (await webhook(rig, nome, email, pedido=f"p{i}")).status_code == 200
    b = C.balances(ctx.store, pid, ctx.clock())
    assert (b["image"], b["video"], b["audio"]) == (500, 30, 150)
    await rig.login()
    assert (await rig.client.put("/admin/api/billing/settings", json={"credit_pack_units": {"image": 700}, "credit_expiry_days": 10})).status_code == 200
    await webhook(rig, "Segundo Cérebro - Crédito de imagens", email, pedido="p9")
    assert C.balances(ctx.store, pid, ctx.clock())["image"] == 500 + 700
    assert ctx.store.one("SELECT expires_at-granted_at d FROM credits WHERE external_id='p9'")["d"] == 10 * DAY


async def test_credito_por_id_de_produto_e_pagamento_pendente_nao_concede(rig2):
    rig = rig2
    pid, _k, email = await assinante(rig, "light")
    ctx = rig.app.state.ctx
    await rig.login()
    await rig.client.put("/admin/api/billing/settings", json={"credit_products": {"image": "prod-img-1"}})
    corpo = {"event": "pix_gerado", "data": {"id": "o1", "customer": {"name": "A", "email": email}, "product": {"id": "prod-img-1", "name": "Qualquer nome"}, "amount": 100.0}}
    h = {"content-type": "application/json", "x-webhook-secret": SEG}
    await rig.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers=h)
    assert C.balances(ctx.store, pid, ctx.clock())["image"] == 0  # so a aprovacao concede
    corpo["event"] = "purchase_approved"
    corpo["data"]["transaction_id"] = "t2"
    await rig.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers=h)
    assert C.balances(ctx.store, pid, ctx.clock())["image"] == 500


async def test_credito_pago_com_email_sem_conta_nao_cria_conta_e_fica_registrado(rig2):
    rig = rig2
    ctx = rig.app.state.ctx
    r = await webhook(rig, "Segundo Cérebro - Crédito de uso", "ninguem@x.com")
    assert r.status_code == 500  # a Cakto tenta de novo; o motivo fica no evento
    assert ctx.store.one("SELECT COUNT(*) n FROM accounts")["n"] == 0
    assert "sem conta" in ctx.store.one("SELECT error FROM billing_events ORDER BY id DESC LIMIT 1")["error"]


# ---------------------------------------------------------------- rota do cliente e painel

async def test_rota_de_creditos_do_cliente_traz_pacotes_checkout_com_email_e_proximo_plano(rig2):
    rig = rig2
    pid, key, email = await assinante(rig, "light")
    h = {"Authorization": f"Bearer {key}"}
    j = (await rig.client.get("/v1/credits", headers=h)).json()
    assert j["plan"] == "light" and j["next_plan"]["id"] == "max" and j["next_plan"]["url"].endswith("/#planos")
    assert [p["kind"] for p in j["packs"]] == ["tokens", "image", "video", "audio"] and all(p["checkout_url"] is None and p["price_usd"] == 18 and p["currency"] == "USD" for p in j["packs"])
    assert j["packs"][0]["units"] == 2_000_000  # 2 x a sessao do Lite
    await rig.login()
    await rig.client.put("/admin/api/billing/settings", json={"credit_offers": {"image": "xyz789"}})
    j = (await rig.client.get("/v1/credits", headers=h)).json()
    img = [p for p in j["packs"] if p["kind"] == "image"][0]
    assert img["checkout_url"] == "https://pay.cakto.com.br/xyz789?email=light%40x.com"
    ultra = await assinante(rig, "ultra")
    assert (await rig.client.get("/v1/credits", headers={"Authorization": f"Bearer {ultra[1]}"})).json()["next_plan"]["url"].startswith("https://wa.me/")
    assert (await rig.client.get("/v1/credits")).status_code == 401
    # cotacao mudou no painel: o preco em dolar acompanha (R$ 100 / 4 = US$ 25); preco fixo vence a cotacao
    await rig.client.put("/admin/api/billing/settings", json={"usd_brl_rate": 4})
    assert (await rig.client.get("/v1/credits", headers=h)).json()["packs"][0]["price_usd"] == 25
    await rig.client.put("/admin/api/billing/settings", json={"credit_price_usd": 20})
    assert (await rig.client.get("/v1/credits", headers=h)).json()["packs"][0]["price_usd"] == 20
    assert (await rig.client.put("/admin/api/billing/settings", json={"usd_brl_rate": 0})).status_code == 422


async def test_painel_valida_ajustes_e_revoga_pacote(rig2):
    rig = rig2
    pid, _k, _ = await assinante(rig, "light")
    await rig.login()
    for ruim in ({"credit_offers": {"lixo": "x"}}, {"credit_pack_units": {"image": 0}}, {"credit_expiry_days": 0}, {"credit_products": []}):
        assert (await rig.client.put("/admin/api/billing/settings", json=ruim)).status_code == 422
    g = (await rig.client.post(f"/admin/api/people/{pid}/credits", json={"kind": "image"})).json()  # sem units: tamanho do pacote
    assert g["units"] == 500
    assert (await rig.client.post(f"/admin/api/people/{pid}/credits", json={"kind": "lixo"})).status_code == 422
    assert (await rig.client.delete(f"/admin/api/credits/{g['id']}")).status_code == 200
    assert (await rig.client.delete(f"/admin/api/credits/{g['id']}")).status_code == 404
    assert (await rig.client.get(f"/admin/api/people/{pid}/credits")).json()["balances"]["image"] == 0
