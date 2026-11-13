"""Painel de fornecedores (spec 36): chave cifrada so de escrita, saldo da Runware ao vivo, precos editaveis com limites."""
import httpx
import pytest_asyncio

from app import fornecedores as F
from tests.conftest import Rig

CHAVE = "rw-chave-do-painel-1234"


@pytest_asyncio.fixture
async def rig_f(tmp_path):
    r = Rig(tmp_path, runware_url="http://up/runware", secrets_key="segredo-mestre")
    await r.start()

    async def handler(request: httpx.Request, body):
        if request.url.path == "/runware":
            if request.headers["authorization"] != f"Bearer {CHAVE}":
                return httpx.Response(401, json={"errors": [{"message": "invalid key"}]})
            return httpx.Response(200, json={"data": [{"taskType": "accountManagement", "balance": 12.5, "organizationName": "Rangel Tech",
                                                       "usage": {"today": {"credits": 0.4, "requests": 3}, "last30Days": {"credits": 7.1, "requests": 90}}}]})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


async def test_painel_exige_login(rig_f: Rig):
    assert (await rig_f.client.get("/admin/api/fornecedores")).status_code == 401
    assert (await rig_f.client.post("/admin/api/fornecedores/chave", json={"fornecedor": "runware", "chave": CHAVE})).status_code == 401


async def test_chave_invalida_nao_e_gravada(rig_f: Rig):
    await rig_f.login()
    r = await rig_f.client.post("/admin/api/fornecedores/chave", json={"fornecedor": "runware", "chave": "errada"})
    assert r.status_code == 422 and "chave recusada" in r.json()["error"]["message"]
    assert rig_f.app.state.ctx.store.get_setting("provider_keys") in (None, "", "{}")


async def test_chave_valida_fica_cifrada_e_mostra_saldo(rig_f: Rig):
    await rig_f.login()
    r = await rig_f.client.post("/admin/api/fornecedores/chave", json={"fornecedor": "runware", "chave": CHAVE})
    assert r.status_code == 200, r.text
    rw = r.json()["fornecedores"][0]
    assert rw["chave"] == "••••1234" and rw["origem"] == "painel" and rw["saldo"]["saldo_usd"] == 12.5 and rw["saldo"]["gasto_30d_usd"] == 7.1
    bruto = rig_f.app.state.ctx.store.get_setting("provider_keys")
    assert CHAVE not in bruto
    assert F.chave(rig_f.app.state.ctx, "runware") == (CHAVE, "painel")
    assert (await rig_f.client.get("/v1/estudio/catalogo")).json()["disponivel"] is True
    await rig_f.client.post("/admin/api/fornecedores/chave", json={"fornecedor": "runware", "chave": ""})
    assert (await rig_f.client.get("/v1/estudio/catalogo")).json()["disponivel"] is False


async def test_precos_editaveis_com_limites(rig_f: Rig):
    await rig_f.login()
    r = await rig_f.client.post("/admin/api/fornecedores/precos", json={"markup": 4, "cap_brl": 25, "por_efeito": {"voz": 8}})
    assert r.status_code == 200 and r.json()["precos"]["markup"] == 4 and r.json()["precos"]["por_efeito"] == {"voz": 8.0}
    voz = next(t for t in r.json()["tabela"] if t["efeito"] == "voz")
    assert voz["exemplo"]["markup_aplicado"] == 8.0
    assert (await rig_f.client.post("/admin/api/fornecedores/precos", json={"markup": 0.5})).status_code == 422
    assert (await rig_f.client.post("/admin/api/fornecedores/precos", json={"markup": 2, "markup_minimo": 3})).status_code == 422


async def test_sem_secrets_key_avisa(tmp_path):
    r = Rig(tmp_path)
    await r.start()
    try:
        await r.login()
        try:
            F.salvar_chave(r.app.state.ctx, "runware", "qualquer")
            assert False, "devia recusar sem SECRETS_KEY"
        except F.FornecedorErro as e:
            assert "SECRETS_KEY" in str(e)
        assert (await r.client.get("/admin/api/fornecedores")).json()["secrets_key_set"] is False
    finally:
        await r.stop()
