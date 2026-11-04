"""Contas, assinatura (webhook da Cakto), CRM e login do app. Os campos do webhook sao a leitura tolerante: ajustar com um evento real."""
import json

from tests.conftest import BODY, Rig

SEG = "seg-cakto-teste"
H = {"x-webhook-secret": SEG}


def compra(email="ana@exemplo.com", plano="Segundo Cérebro Max", evento="purchase_approved", sub="sub-1", ev="ev-1", proximo="2026-12-04T10:00:00Z", valor=500.0):
    return {"event": evento, "data": {"id": ev, "customer": {"name": "Ana Souza", "email": email}, "product": {"name": plano}, "amount": valor,
                                      "subscription": {"id": sub, "next_payment_date": proximo}}}


async def webhook(rig: Rig, payload, headers=H):
    return await rig.client.post("/v1/billing/cakto", content=json.dumps(payload), headers={"content-type": "application/json", **headers})


async def lista(rig: Rig, **q):
    return (await rig.client.get("/admin/api/subscribers", params=q)).json()


async def liberar(rig: Rig, sid: int):
    return (await rig.client.post(f"/admin/api/subscribers/{sid}/enable")).json()


async def definir_senha(rig: Rig, sid: int, senha="senha-forte-123"):
    link = (await rig.client.post(f"/admin/api/subscribers/{sid}/password-link")).json()["setup_link"]
    r = await rig.client.post("/v1/auth/set-password", json={"token": link.split("token=")[1], "password": senha})
    assert r.status_code == 200, r.text


async def login(rig: Rig, email="ana@exemplo.com", senha="senha-forte-123", device="PC da Ana"):
    return await rig.client.post("/v1/auth/login", json={"email": email, "password": senha, "device": device})


# ---------------------------------------------------------------- webhook

async def test_webhook_sem_segredo_configurado_nao_fica_aberto(rig: Rig):
    assert (await webhook(rig, compra())).status_code == 503


async def test_webhook_exige_o_segredo(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    assert (await webhook(r, compra(), headers={})).status_code == 401
    assert (await webhook(r, compra(), headers={"x-webhook-secret": "errado"})).status_code == 401
    assert (await webhook(r, compra(), headers={"authorization": f"Bearer {SEG}"})).status_code == 200


async def test_webhook_aceita_assinatura_hmac(make_rig):
    import hashlib, hmac
    r = await make_rig(cakto_webhook_secret=SEG)
    raw = json.dumps(compra()).encode()
    sig = hmac.new(SEG.encode(), raw, hashlib.sha256).hexdigest()
    resp = await r.client.post("/v1/billing/cakto", content=raw, headers={"content-type": "application/json", "x-signature": "sha256=" + sig})
    assert resp.status_code == 200


async def test_compra_cria_conta_e_pessoa_desabilitada(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    assert (await webhook(r, compra())).json() == {"ok": True, "duplicate": False, "status": "active"}
    await r.login()
    d = await lista(r)
    (a,) = d["rows"]
    assert a["email"] == "ana@exemplo.com" and a["plan"] == "max" and a["status"] == "active" and a["enabled"] is False  # nasce desabilitada
    assert a["next_payment_at"].startswith("2026-12-04") and a["amount_cents"] == 50000 and a["safety_level"] == 1
    assert d["summary"] == {"total": 1, "active": 1, "late": 0, "ended": 0, "awaiting": 1, "mrr_cents": 50000}
    pessoa = (await r.client.get("/admin/api/people")).json()[0]
    assert pessoa["limits"]["session_limit_tokens"] == 3_000_000  # limites do plano Max


async def test_evento_repetido_nao_duplica(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    assert (await webhook(r, compra())).json()["duplicate"] is True
    await r.login()
    assert len((await lista(r))["rows"]) == 1
    assert (await r.client.get("/admin/api/billing/events")).json()["rows"][0]["processed"] is True


async def test_evento_desconhecido_ou_sem_email_so_fica_no_registro(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    assert (await webhook(r, {"event": "algo_novo", "data": {"id": "x1", "customer": {"email": "a@b.com"}}})).status_code == 200
    assert (await webhook(r, compra(email="", ev="ev-9"))).status_code == 200
    await r.login()
    assert (await lista(r))["rows"] == []


async def test_plano_pelo_nome_do_produto(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    for i, (nome, esperado) in enumerate([("Plano Ultra", "ultra"), ("Pro mensal", "pro"), ("Ultra Max", "ultra"), ("Plano Max", "max")]):
        await webhook(r, compra(email=f"u{i}@x.com", plano=nome, ev=f"e{i}", sub=f"s{i}"))
    await r.login()
    assert {x["email"]: x["plan"] for x in (await lista(r))["rows"]} == {"u0@x.com": "ultra", "u1@x.com": "pro", "u2@x.com": "ultra", "u3@x.com": "max"}


# ---------------------------------------------------------------- login: so assinante liberado entra

async def test_fluxo_completo_compra_senha_liberacao_login(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    await r.login()
    sid = (await lista(r))["rows"][0]["id"]
    # antes de definir a senha nao entra
    assert (await login(r)).status_code == 401
    await definir_senha(r, sid)
    # senha definida, mas a conta ainda nao foi liberada pelo administrador
    resp = await login(r)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "awaiting_release"
    await liberar(r, sid)
    resp = await login(r)
    assert resp.status_code == 200
    j = resp.json()
    assert j["api_key"].startswith("sk-") and j["plan"] == {"id": "max", "name": "Max"} and j["account"]["email"] == "ana@exemplo.com"
    # a chave emitida funciona de verdade no chat
    ok = await r.client.post("/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {j['api_key']}"})
    assert ok.status_code == 200
    # a chave nao fica em texto no banco
    assert j["api_key"] not in json.dumps([dict(x) for x in r.app.state.ctx.store.query("SELECT * FROM keys")], default=str)


async def test_credenciais_erradas_respondem_igual(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    await r.login()
    await definir_senha(r, (await lista(r))["rows"][0]["id"])
    a = await login(r, senha="errada-123")
    b = await login(r, email="ninguem@x.com")
    assert a.status_code == b.status_code == 401 and a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


async def test_limite_de_computadores_revoga_a_chave_mais_antiga(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    await r.login()
    sid = (await lista(r))["rows"][0]["id"]
    await definir_senha(r, sid)
    await liberar(r, sid)
    await r.client.put("/admin/api/plans/max", json={"max_devices": 2})
    k1 = (await login(r, device="PC 1")).json()["api_key"]
    k2 = (await login(r, device="PC 2")).json()["api_key"]
    k3 = (await login(r, device="PC 3")).json()["api_key"]
    chat = lambda k: r.client.post("/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {k}"})  # noqa: E731
    assert (await chat(k1)).status_code == 401  # a mais antiga caiu
    assert (await chat(k2)).status_code == 200 and (await chat(k3)).status_code == 200
    assert (await lista(r))["rows"][0]["devices"] == 2


async def test_senha_fraca_e_link_de_uso_unico(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    await r.login()
    sid = (await lista(r))["rows"][0]["id"]
    tok = (await r.client.post(f"/admin/api/subscribers/{sid}/password-link")).json()["setup_link"].split("token=")[1]
    assert (await r.client.post("/v1/auth/set-password", json={"token": tok, "password": "curta"})).status_code == 422
    assert (await r.client.post("/v1/auth/set-password", json={"token": tok, "password": "senha-forte-123"})).status_code == 200
    assert (await r.client.post("/v1/auth/set-password", json={"token": tok, "password": "outra-senha-123"})).status_code == 400  # ja usado


async def test_tentativas_demais_de_login_sao_barradas(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    codes = [(await login(r, email="spam@x.com", senha="x" * 9)).status_code for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429


# ---------------------------------------------------------------- pagamento: atraso, cancelamento, volta

async def _assinante_liberado(r: Rig) -> int:
    await webhook(r, compra())
    await r.login()
    sid = (await lista(r))["rows"][0]["id"]
    await definir_senha(r, sid)
    await liberar(r, sid)
    return sid


async def test_cancelamento_bloqueia_na_hora(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await _assinante_liberado(r)
    key = (await login(r)).json()["api_key"]
    await webhook(r, compra(evento="subscription_canceled", ev="ev-2"))
    a = (await lista(r))["rows"][0]
    assert a["status"] == "canceled" and a["enabled"] is False
    assert (await r.client.post("/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {key}"})).status_code == 403  # a chave ja emitida tambem para
    assert (await login(r)).json()["error"]["code"] == "subscription_inactive"


async def test_reembolso_e_chargeback_encerram(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await _assinante_liberado(r)
    await webhook(r, compra(evento="chargeback", ev="ev-3"))
    assert (await lista(r))["rows"][0]["status"] == "chargeback"
    assert (await lista(r))["summary"]["ended"] == 1


async def test_atraso_tem_carencia_e_depois_bloqueia_e_pagando_volta(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    from app.accounts import _ts
    await _assinante_liberado(r)
    r.clock.t = _ts("2026-11-04T09:00:00Z")  # o atraso chega no dia do vencimento
    await webhook(r, compra(evento="subscription_late", ev="ev-4", proximo="2026-11-04T10:00:00Z"))
    assert (await lista(r))["rows"][0]["status"] == "late"
    # a carencia (3 dias) e medida a partir do vencimento: um dia depois ainda entra
    r.clock.t = _ts("2026-11-04T10:00:00Z") + 86400
    assert (await login(r)).status_code == 200
    r.clock.t = _ts("2026-11-04T10:00:00Z") + 4 * 86400  # passou dos 3 dias
    assert (await login(r)).json()["error"]["code"] == "subscription_inactive"
    a = (await lista(r))["rows"][0]
    assert a["enabled"] is False and a["blocked_by_billing"] is True
    await webhook(r, compra(evento="purchase_approved", ev="ev-5", proximo="2027-01-04T10:00:00Z"))  # voltou a pagar
    a = (await lista(r))["rows"][0]
    assert a["status"] == "active" and a["enabled"] is True and a["blocked_by_billing"] is False  # reabre sozinho: o admin ja tinha liberado
    assert (await login(r)).status_code == 200


async def test_primeira_compra_nunca_libera_sozinha_sem_a_opcao(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    await r.login()
    assert (await lista(r))["rows"][0]["enabled"] is False
    assert (await r.client.put("/admin/api/billing/settings", json={"auto_enable": True})).json()["auto_enable"] is True
    await webhook(r, compra(email="bia@x.com", ev="e2", sub="s2"))
    await webhook(r, compra(email="bia@x.com", ev="e3", sub="s2", evento="renewed"))  # renovacao com a opcao ligada
    bia = next(x for x in (await lista(r))["rows"] if x["email"] == "bia@x.com")
    assert bia["enabled"] is True


# ---------------------------------------------------------------- CRM (painel)

async def test_crm_exige_login_do_admin(rig: Rig):
    for m, p in (("get", "/admin/api/subscribers"), ("post", "/admin/api/subscribers"), ("get", "/admin/api/plans"), ("get", "/admin/api/billing/settings"), ("get", "/admin/api/billing/events")):
        assert (await getattr(rig.client, m)(p)).status_code == 401


async def test_cadastro_manual_busca_e_filtro(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await r.login()
    c = await r.client.post("/admin/api/subscribers", json={"name": "Cortesia", "email": "cortesia@x.com", "plan": "pro", "enable": True, "safety_level": 2})
    assert c.status_code == 200 and c.json()["subscriber"]["enabled"] is True and c.json()["subscriber"]["safety_level"] == 2 and "definir-senha?token=" in c.json()["setup_link"]
    assert (await r.client.post("/admin/api/subscribers", json={"name": "x", "email": "cortesia@x.com"})).status_code == 409
    assert (await r.client.post("/admin/api/subscribers", json={"name": "x", "email": "sem-arroba"})).status_code == 422
    assert (await r.client.post("/admin/api/subscribers", json={"name": "x", "email": "a@b.com", "plan": "nao-existe"})).status_code == 422
    await webhook(r, compra())
    assert len((await lista(r, q="cortesia"))["rows"]) == 1
    assert [x["email"] for x in (await lista(r, status="awaiting"))["rows"]] == ["ana@exemplo.com"]


async def test_planos_editaveis_e_validados(rig: Rig):
    await rig.login()
    planos = (await rig.client.get("/admin/api/plans")).json()["plans"]
    assert [p["id"] for p in planos] == ["pro", "max", "ultra"] and planos[0]["price_cents"] == 25000
    assert (await rig.client.put("/admin/api/plans/pro", json={"price_cents": 29900, "week_limit_tokens": 12_000_000})).json()["price_cents"] == 29900
    for ruim in ({"safety_level": 3}, {"max_devices": 0}, {"price_cents": -1}, {"nome": "x"}, {"name": "  "}):
        assert (await rig.client.put("/admin/api/plans/pro", json=ruim)).status_code == 422
    assert (await rig.client.put("/admin/api/plans/nao-existe", json={"price_cents": 1})).status_code == 404


async def test_configuracao_de_cobranca(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await r.login()
    s = (await r.client.get("/admin/api/billing/settings")).json()
    assert s["grace_days"] == 3 and s["auto_enable"] is False and s["webhook_configured"] is True
    assert (await r.client.put("/admin/api/billing/settings", json={"grace_days": 7, "plan_map": {"meu produto": "ultra"}})).json()["grace_days"] == 7
    assert (await r.client.put("/admin/api/billing/settings", json={"grace_days": 99})).status_code == 422
    await webhook(r, compra(plano="Meu Produto Especial"))
    assert (await lista(r))["rows"][0]["plan"] == "ultra"


async def test_esqueci_a_senha_nao_revela_se_existe(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await webhook(r, compra())
    a = await r.client.post("/v1/auth/forgot", json={"email": "ana@exemplo.com"})
    b = await r.client.post("/v1/auth/forgot", json={"email": "ninguem@x.com"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
