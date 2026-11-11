"""Teste gratis: cadastro com codigo, 5 perguntas por pessoa, sem chave de API, promocao pelo webhook com o mesmo e-mail."""
import json
import re

from app import free
from tests.conftest import Rig
from tests.test_accounts import compra, webhook


async def cadastrar(rig: Rig, email="gabi@exemplo.com", senha="senha-boa-123", **kw):
    return await rig.client.post("/v1/free/signup", json={"email": email, "password": senha, "name": "Gabi", "aceite_termos": True, **kw})


def codigo_de(rig: Rig, email="gabi@exemplo.com") -> str:
    """O codigo so existe em hash; o teste troca por um conhecido (o envio real e por SMTP)."""
    ctx = rig.app.state.ctx
    acc = ctx.store.one("SELECT id FROM accounts WHERE email=?", (email,))
    with ctx.store.tx() as c:
        c.execute("UPDATE free_trials SET code_hash=?, code_expires=?, code_tries=0 WHERE account_id=?", (free._hash_codigo("123456", acc["id"]), ctx.clock() + 600, acc["id"]))
    return "123456"


async def entrar(rig: Rig, email="gabi@exemplo.com"):
    assert (await cadastrar(rig, email)).status_code == 200
    r = await rig.client.post("/v1/free/verify", json={"email": email, "code": codigo_de(rig, email)})
    assert r.status_code == 200, r.text
    return r.json()


def auth(tok):
    return {"authorization": "Bearer " + tok}


async def perguntar(rig: Rig, tok, texto="oi"):
    return await rig.client.post("/v1/free/chat", json={"messages": [{"role": "user", "content": texto}]}, headers=auth(tok))


async def test_cadastro_exige_codigo_antes_de_perguntar(rig: Rig):
    r = await cadastrar(rig)
    assert r.status_code == 200 and r.json()["need_code"] is True
    login = await rig.client.post("/v1/free/login", json={"email": "gabi@exemplo.com", "password": "senha-boa-123"})
    assert login.status_code == 403 and login.json()["error"]["code"] == "need_code"
    assert rig.up.ai_calls == 0


async def test_codigo_errado_e_certo(rig: Rig):
    await cadastrar(rig)
    codigo_de(rig)
    r = await rig.client.post("/v1/free/verify", json={"email": "gabi@exemplo.com", "code": "000000"})
    assert r.status_code == 400
    r = await rig.client.post("/v1/free/verify", json={"email": "gabi@exemplo.com", "code": "123456"})
    assert r.status_code == 200 and r.json()["remaining"] == 5 and r.json()["token"]


async def test_cinco_perguntas_e_a_sexta_pede_plano(rig: Rig):
    tok = (await entrar(rig))["token"]
    for i in range(5):
        r = await perguntar(rig, tok, f"pergunta {i}")
        assert r.status_code == 200, r.text
        assert "ola mundo" in r.text.replace('"', "").replace("ola", "ola") or "ola" in r.text
        ultimo = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: {") and '"free"' in l][-1]["free"]
        assert ultimo["remaining"] == 4 - i
    r = await perguntar(rig, tok, "sexta")
    assert r.status_code == 402 and r.json()["error"]["code"] == "free_limit"
    assert rig.up.ai_calls == 5  # a sexta nem chega ao modelo
    assert (await rig.client.get("/v1/free/me", headers=auth(tok))).json()["remaining"] == 0


async def test_chamada_ao_modelo_vai_sem_raciocinio_com_limite_de_tokens_e_prompt_de_teste(rig: Rig):
    tok = (await entrar(rig))["token"]
    await perguntar(rig, tok, "oi")
    _path, body, headers = rig.up.calls[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] <= 900 and "tools" not in body
    assert body["messages"][0]["role"] == "system" and "teste grátis" in body["messages"][0]["content"]
    assert headers["x-end-user"].startswith("gratis-")


async def test_falha_do_modelo_nao_gasta_a_pergunta(rig: Rig):
    import httpx
    async def cai(request, body):
        return httpx.Response(500, json={"error": "x"})
    rig.up.handler = cai
    tok = (await entrar(rig))["token"]
    r = await perguntar(rig, tok)
    assert r.status_code == 502
    assert (await rig.client.get("/v1/free/me", headers=auth(tok))).json()["remaining"] == 5


async def test_mesmo_email_com_etiqueta_nao_ganha_outro_teste(rig: Rig):
    await entrar(rig, "gabi@exemplo.com")
    r = await cadastrar(rig, "gabi+outra@exemplo.com")
    assert r.status_code == 409
    g1, g2 = await cadastrar(rig, "jo.ao@gmail.com"), await cadastrar(rig, "joao+x@gmail.com")
    assert g1.status_code == 200 and g2.status_code == 409


async def test_email_descartavel_senha_fraca_e_isca_sao_barrados(rig: Rig):
    assert (await cadastrar(rig, "x@mailinator.com")).status_code == 400
    assert (await cadastrar(rig, "y@exemplo.com", senha="123")).status_code == 400
    r = await cadastrar(rig, "z@exemplo.com", website="http://spam")
    assert r.status_code == 200
    assert rig.app.state.ctx.store.one("SELECT 1 FROM accounts WHERE email='z@exemplo.com'") is None


async def test_limite_de_cadastros_por_endereco(rig: Rig):
    for i in range(4):
        assert (await cadastrar(rig, f"p{i}@exemplo.com")).status_code == 200
    assert (await cadastrar(rig, "p9@exemplo.com")).status_code == 429


async def test_token_invalido_ou_adulterado_nao_entra(rig: Rig):
    tok = (await entrar(rig))["token"]
    assert (await perguntar(rig, "lixo")).status_code == 401
    assert (await perguntar(rig, tok[:-3] + "aaa")).status_code == 401
    assert (await rig.client.post("/v1/free/chat", json={"messages": []}, headers=auth(tok))).status_code == 400


async def test_quem_compra_com_o_mesmo_email_vira_assinante_na_mesma_conta(rig: Rig):
    await entrar(rig, "gabi@exemplo.com")
    ctx = rig.app.state.ctx
    antes = ctx.store.one("SELECT COUNT(*) n FROM accounts")["n"]
    r = await rig.client.post("/admin/api/login", json={"password": "senha-do-admin-123"})
    await rig.client.put("/admin/api/billing/settings", json={"webhook_secret": "seg"}) if False else None
    ctx.s.cakto_webhook_secret = "seg-cakto-teste"
    assert (await webhook(rig, compra(email="gabi@exemplo.com"))).status_code == 200
    assert ctx.store.one("SELECT COUNT(*) n FROM accounts")["n"] == antes  # nenhuma conta nova
    login = await rig.client.post("/v1/free/login", json={"email": "gabi@exemplo.com", "password": "senha-boa-123"})
    assert login.json().get("paid") is True  # agora usa o app; o teste nao se aplica mais


async def test_cors_so_para_as_origens_do_site(rig: Rig):
    r = await rig.client.options("/v1/free/chat", headers={"origin": "https://brain.rangeltech.net", "access-control-request-method": "POST", "access-control-request-headers": "authorization,content-type"})
    assert r.headers.get("access-control-allow-origin") == "https://brain.rangeltech.net"
    r = await rig.client.options("/v1/free/chat", headers={"origin": "https://malvado.exemplo", "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in r.headers
