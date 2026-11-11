"""Termos com aceite verificavel, afiliados/cupom e checkout embutido (Spec 25 §18-§19)."""
import json

import pytest
import pytest_asyncio

from app import terms
from tests.conftest import Rig

SEG = "seg-cakto-teste"


@pytest_asyncio.fixture
async def rig(tmp_path):
    r = Rig(tmp_path, cakto_webhook_secret=SEG)
    await r.start()
    yield r
    await r.stop()


async def hook(rig, payload):
    return await rig.client.post("/v1/billing/cakto", content=json.dumps(payload), headers={"content-type": "application/json", "x-webhook-secret": SEG})


def _webhook_aprovado(email, callback=None, offer=None, produto="Segundo Cérebro Max", ev_id="e1"):
    data = {"customer": {"email": email, "name": "Cli"}, "product": {"id": "p1", "name": produto}, "amount": 497.0, "id": ev_id,
            "subscription": {"id": "sub-" + ev_id}}
    if callback:
        data["callback"] = callback
    if offer:
        data["offer"] = {"id": offer}
    return {"event": "purchase_approved", "event_id": ev_id, "data": data}


async def test_termos_texto_e_hash(rig: Rig):
    r = await rig.client.get("/v1/termos")
    j = r.json()
    assert r.status_code == 200 and j["versao"] == "2026-11-10" and "Usos proibidos" in j["texto"]
    assert j["sha256"] == terms._sha(j["texto"])
    assert "conteúdo adulto" in j["texto"].lower() and "3 (três) meses" in j["texto"]


async def test_aceite_encadeado_assinado_e_verificavel(rig: Rig):
    ctx = rig.app.state.ctx
    a = terms.registrar(ctx, "a@x.com", "site", ip="1.1.1.1", enviar_comprovante=False)
    b = terms.registrar(ctx, "b@x.com", "checkout", ip="2.2.2.2", enviar_comprovante=False)
    assert a["novo"] and b["novo"] and a["hash"] != b["hash"]
    assert terms.registrar(ctx, "a@x.com", "site", enviar_comprovante=False)["novo"] is False  # mesma versao nao duplica
    linhas = ctx.store.query("SELECT * FROM terms_acceptances ORDER BY id")
    assert linhas[1]["hash_anterior"] == linhas[0]["hash"]
    assert terms.verificar_corrente(ctx)["ok"]
    v = (await rig.client.get("/v1/termos/verificar", params={"codigo": b["codigo"]})).json()
    assert v["valido"] is True and v["email"] == "b***@x.com" and v["canal"] == "checkout"
    assert (await rig.client.get("/v1/termos/verificar", params={"codigo": "0" * 12})).json() == {"valido": False}


async def test_aceite_nao_pode_ser_alterado_nem_apagado(rig: Rig):
    ctx = rig.app.state.ctx
    terms.registrar(ctx, "a@x.com", "site", enviar_comprovante=False)
    with pytest.raises(Exception):
        with ctx.store.tx() as c:
            c.execute("UPDATE terms_acceptances SET email='outro@x.com'")
    with pytest.raises(Exception):
        with ctx.store.tx() as c:
            c.execute("DELETE FROM terms_acceptances")
    assert terms.verificar_corrente(ctx)["ok"]


async def test_adulteracao_e_detectada(rig: Rig):
    ctx = rig.app.state.ctx
    terms.registrar(ctx, "a@x.com", "site", enviar_comprovante=False)
    terms.registrar(ctx, "b@x.com", "site", enviar_comprovante=False)
    # burla o trigger de proposito (dono do banco): a verificacao tem que pegar
    ctx.store._conn().execute("DROP TRIGGER terms_acc_no_upd")
    ctx.store._conn().execute("UPDATE terms_acceptances SET email='falso@x.com' WHERE id=1")
    r = terms.verificar_corrente(ctx)
    assert not r["ok"] and any("hash" in p["erro"] for p in r["problemas"])


async def test_set_password_exige_aceite_em_conta_nova(rig: Rig):
    await rig.login()
    r = await hook(rig, _webhook_aprovado("novo@x.com"))
    assert r.status_code == 200
    link = (await rig.client.post("/admin/api/subscribers/1/password-link")).json()["setup_link"]
    tok = link.split("token=")[1]
    sem = await rig.client.post("/v1/auth/set-password", json={"token": tok, "password": "senha-forte-123"})
    assert sem.status_code == 400 and sem.json()["error"]["code"] == "terms_required"
    ok = await rig.client.post("/v1/auth/set-password", json={"token": tok, "password": "senha-forte-123", "aceite_termos": True})
    assert ok.status_code == 200
    ctx = rig.app.state.ctx
    assert terms.aceitou(ctx, "novo@x.com") == "2026-11-10"
    adm = (await rig.client.get("/admin/api/termos/aceites", params={"email": "novo@x.com"})).json()["aceites"]
    assert len(adm) == 1 and adm[0]["canal"] == "site"
    assert (await rig.client.get("/admin/api/termos/integridade")).json()["ok"]


async def test_free_signup_exige_aceite(rig: Rig):
    base = {"email": "t@x.com", "password": "senha-forte-123", "name": "T"}
    r = await rig.client.post("/v1/free/signup", json=base)
    assert r.status_code == 400 and r.json()["error"]["code"] == "terms_required"
    assert (await rig.client.post("/v1/free/signup", json={**base, "aceite_termos": True})).status_code == 200


# ------------------------------------------------------------------ afiliados

async def _afiliado(rig, **kw):
    corpo = {"nome": "Fulano Influencer", "cupom": "FULANO10", "ativo": True,
             "links": [{"produto": "max_mensal", "url": "https://pay.cakto.com.br/ofmax?affiliate=afil123", "preco": 447.30},
                       {"produto": "max_anual", "url": "https://pay.cakto.com.br/ofmaxa?affiliate=afil123", "preco": "4473,00"}], **kw}
    r = await rig.client.post("/admin/api/afiliados", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


async def test_afiliado_cupom_percentual_e_so_produtos_com_link(rig: Rig):
    await rig.login()
    a = await _afiliado(rig)
    por = {l["produto"]: l for l in a["links"]}
    assert por["max_mensal"]["cakto_offer_id"] == "ofmax" and por["max_mensal"]["cakto_affiliate_id"] == "afil123"
    j = (await rig.client.get("/v1/cupom/fulano10")).json()  # sem diferenca entre maiuscula e minuscula
    assert j["ok"] and j["afiliado"] == "Fulano Influencer"
    assert set(j["produtos"]) == {"max_mensal", "max_anual"}  # lite, ultra etc. nao valem
    assert j["produtos"]["max_mensal"]["percentual"] == 10 and j["produtos"]["max_mensal"]["preco_normal"] == 497.0
    assert j["produtos"]["max_anual"]["preco_normal"] == 4970.0 and j["produtos"]["max_anual"]["percentual"] == 10
    assert (await rig.client.get("/v1/cupom/naoexiste")).json()["motivo"] == "nao_encontrado"


async def test_afiliado_liga_desliga_sem_apagar(rig: Rig):
    await rig.login()
    a = await _afiliado(rig)
    r = await rig.client.put(f"/admin/api/afiliados/{a['id']}", json={"ativo": False})
    assert r.status_code == 200 and r.json()["ativo"] is False and len(r.json()["links"]) == 2  # links ficam
    assert (await rig.client.get("/v1/cupom/FULANO10")).json()["motivo"] == "inativo"
    await rig.client.put(f"/admin/api/afiliados/{a['id']}", json={"ativo": True})
    assert (await rig.client.get("/v1/cupom/FULANO10")).json()["ok"]


async def test_afiliado_validacoes(rig: Rig):
    await rig.login()
    await _afiliado(rig)
    dup = await rig.client.post("/admin/api/afiliados", json={"nome": "Outro", "cupom": "fulano10"})
    assert dup.status_code == 409
    assert (await rig.client.post("/admin/api/afiliados", json={"nome": "X", "cupom": "a b"})).status_code == 400
    ruim = await rig.client.post("/admin/api/afiliados", json={"nome": "Y", "cupom": "YYY", "links": [{"produto": "credito_uso", "url": "https://x.com/a", "preco": 10}]})
    assert ruim.status_code == 400  # credito nunca tem afiliado
    sem_https = await rig.client.post("/admin/api/afiliados", json={"nome": "Z", "cupom": "ZZZ", "links": [{"produto": "max_mensal", "url": "http://x.com/a", "preco": 10}]})
    assert sem_https.status_code == 400


async def test_checkout_sessao_com_cupom_e_webhook_fecha_e_atribui(rig: Rig):
    await rig.login()
    a = await _afiliado(rig)
    base = {"email": "Cliente@X.com", "nome": "Cliente", "produto": "max_mensal", "cupom": "fulano10"}
    assert (await rig.client.post("/v1/checkout/sessao", json=base)).status_code == 400  # sem aceite dos termos
    r = await rig.client.post("/v1/checkout/sessao", json={**base, "aceite_termos": True})
    j = r.json()
    assert r.status_code == 200 and j["url_afiliado"].startswith("https://pay.cakto.com.br/ofmax") and j["percentual"] == 10 and j["afiliado"] == "Fulano Influencer"
    tok = j["token"]
    assert (await rig.client.get(f"/v1/checkout/{tok}")).json()["estado"] == "aberto"
    assert terms.aceitou(rig.app.state.ctx, "cliente@x.com") == "2026-11-10"  # o aceite do checkout fica ligado ao e-mail
    r = await hook(rig, _webhook_aprovado("cliente@x.com", callback=tok))
    assert r.status_code == 200
    assert (await rig.client.get(f"/v1/checkout/{tok}")).json()["estado"] == "pago"
    vendas = (await rig.client.get(f"/admin/api/afiliados/{a['id']}/vendas")).json()["vendas"]
    assert len(vendas) == 1 and vendas[0]["email"] == "cliente@x.com" and vendas[0]["origem"] == "sessao"
    lista = (await rig.client.get("/admin/api/afiliados")).json()["afiliados"][0]
    assert lista["vendas"] == 1 and lista["vendas_valor"] == 497.0


async def test_afiliado_so_na_primeira_venda(rig: Rig):
    await rig.login()
    a = await _afiliado(rig)
    j = (await rig.client.post("/v1/checkout/sessao", json={"email": "c@x.com", "produto": "max_mensal", "cupom": "FULANO10", "aceite_termos": True})).json()
    await hook(rig, _webhook_aprovado("c@x.com", callback=j["token"], ev_id="e1"))
    ren = _webhook_aprovado("c@x.com", ev_id="e2")
    ren["event"] = "subscription_renewed"
    await hook(rig, ren)
    assert len((await rig.client.get(f"/admin/api/afiliados/{a['id']}/vendas")).json()["vendas"]) == 1  # renovacao nao conta


async def test_venda_pela_oferta_do_afiliado_sem_sessao(rig: Rig):
    """Comprou direto no link do afiliado (fora do nosso checkout): atribui pela oferta."""
    await rig.login()
    a = await _afiliado(rig)
    await hook(rig, _webhook_aprovado("d@x.com", offer="ofmax"))
    v = (await rig.client.get(f"/admin/api/afiliados/{a['id']}/vendas")).json()["vendas"]
    assert len(v) == 1 and v[0]["origem"] == "oferta"


async def test_cupom_inativo_ou_sem_link_nao_da_url(rig: Rig):
    await rig.login()
    a = await _afiliado(rig)
    sem_link = await rig.client.post("/v1/checkout/sessao", json={"email": "e@x.com", "produto": "ultra_mensal", "cupom": "FULANO10", "aceite_termos": True})
    assert sem_link.json()["url_afiliado"] is None  # cupom nao vale para o Ultra
    await rig.client.put(f"/admin/api/afiliados/{a['id']}", json={"ativo": False})
    off = await rig.client.post("/v1/checkout/sessao", json={"email": "f@x.com", "produto": "max_mensal", "cupom": "FULANO10", "aceite_termos": True})
    assert off.json()["url_afiliado"] is None
    assert (await rig.client.post("/v1/checkout/sessao", json={"email": "g@x.com", "produto": "credito_uso", "aceite_termos": True})).status_code == 400
    assert (await rig.client.get("/v1/checkout/inexistente123")).status_code == 404
