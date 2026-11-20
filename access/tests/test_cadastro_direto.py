"""Spec 53: cadastro direto. Comprou -> entra com e-mail e cria a senha na hora; o e-mail so informa e confirma o endereco."""
from tests.conftest import Rig
from tests.test_accounts import SEG, compra, definir_senha, liberar, lista, login, webhook


async def _comprou(r: Rig, **kw) -> int:
    await webhook(r, compra(**kw))
    await r.login()
    sid = (await lista(r))["rows"][0]["id"]
    await liberar(r, sid)
    return sid


async def test_primeiro_acesso_cria_a_senha_na_hora_e_entra(make_rig, monkeypatch):
    r = await make_rig(cakto_webhook_secret=SEG)
    enviados = []
    from app import mailer
    monkeypatch.setattr(mailer, "send_bem_vindo", lambda ctx, to, name, tok: enviados.append((to, tok)) or True)
    await _comprou(r)
    # sem marcar os termos: pede o aceite e NAO grava a senha
    sem = await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "senha-forte-123", "device": "PC"})
    assert sem.status_code == 400 and sem.json()["error"]["code"] == "terms_required"
    # senha fraca: recusa com o aviso de primeiro acesso
    fraca = await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "123", "aceite_termos": True})
    assert fraca.status_code == 422 and fraca.json()["error"]["code"] == "weak_password"
    ok = await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "senha-forte-123", "device": "PC", "aceite_termos": True})
    assert ok.status_code == 200, ok.text
    j = ok.json()
    assert j["first_access"] is True and j["email_confirmed"] is False and j["api_key"]
    assert enviados and enviados[0][0] == "ana@exemplo.com"
    # a senha ficou: o proximo login e normal; senha errada nao vira "primeiro acesso" de novo
    assert (await login(r)).json()["first_access"] is False
    assert (await login(r, senha="outra-senha-999")).status_code == 401
    # o app sabe que o e-mail ainda nao foi confirmado
    who = (await r.client.get("/v1/auth/whoami", headers={"Authorization": f"Bearer {j['api_key']}"})).json()
    assert who["email_confirmed"] is False
    # link do e-mail confirma
    pag = await r.client.get("/v1/auth/confirmar-email", params={"t": enviados[0][1]})
    assert pag.status_code == 200 and "confirmado" in pag.text
    who = (await r.client.get("/v1/auth/whoami", headers={"Authorization": f"Bearer {j['api_key']}"})).json()
    assert who["email_confirmed"] is True
    assert "vencido" in (await r.client.get("/v1/auth/confirmar-email", params={"t": enviados[0][1]})).text  # uso unico


async def test_sem_compra_ativa_nao_cria_senha(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    await _comprou(r)
    await webhook(r, compra(evento="subscription_canceled", ev="ev-2"))
    x = await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "senha-forte-123", "aceite_termos": True})
    assert x.status_code == 403 and x.json()["error"]["code"] == "subscription_inactive"
    # e-mail que nunca comprou: o mesmo erro de sempre
    y = await r.client.post("/v1/auth/login", json={"email": "ninguem@x.com", "password": "senha-forte-123", "aceite_termos": True})
    assert y.status_code == 401


async def test_quem_ja_tem_senha_nao_troca_pelo_login(make_rig):
    r = await make_rig(cakto_webhook_secret=SEG)
    sid = await _comprou(r)
    await definir_senha(r, sid)  # criou pelo link do e-mail (tambem confirma o e-mail)
    x = await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "senha-nova-456", "aceite_termos": True})
    assert x.status_code == 401
    j = (await login(r)).json()
    assert j["first_access"] is False and j["email_confirmed"] is True


async def test_reenviar_confirmacao(make_rig, monkeypatch):
    r = await make_rig(cakto_webhook_secret=SEG)
    from app import mailer
    monkeypatch.setattr(mailer, "send_bem_vindo", lambda *a: True)
    mandados = []
    monkeypatch.setattr(mailer, "send_confirmar_email", lambda ctx, to, name, tok: mandados.append(tok) or True)
    await _comprou(r)
    key = (await r.client.post("/v1/auth/login", json={"email": "ana@exemplo.com", "password": "senha-forte-123", "aceite_termos": True})).json()["api_key"]
    h = {"Authorization": f"Bearer {key}"}
    assert (await r.client.post("/v1/auth/reenviar-confirmacao", headers=h)).json() == {"ok": True, "email_confirmed": False}
    assert len(mandados) == 1
    await r.client.get("/v1/auth/confirmar-email", params={"t": mandados[0]})
    assert (await r.client.post("/v1/auth/reenviar-confirmacao", headers=h)).json()["email_confirmed"] is True


async def test_acesso_web_tambem_cria_a_senha(make_rig, monkeypatch):
    r = await make_rig(cakto_webhook_secret=SEG)
    from app import mailer
    monkeypatch.setattr(mailer, "send_bem_vindo", lambda *a: True)
    await _comprou(r)
    x = await r.client.post("/v1/auth/verify", json={"email": "ana@exemplo.com", "password": "senha-forte-123", "aceite_termos": True})
    assert x.status_code == 200 and x.json()["first_access"] is True
    assert (await login(r)).status_code == 200
