"""Proxy do navegador por cliente (spec 31): cifrado no banco, mascarado no painel, entregue so ao dono da chave."""
import pytest

from app import proxy_nav as P
from tests.conftest import Rig


@pytest.fixture
async def rig4(make_rig):
    return await make_rig(secrets_key="segredo-de-teste")


async def test_guarda_cifrado_e_entrega_so_a_pessoa(rig4: Rig):
    ctx = rig4.app.state.ctx
    pid, key = await rig4.person("cli-proxy")
    P.guardar(ctx, str(pid), "http://user:senha@br.proxy.exemplo:8000")
    bruto = ctx.store.get_setting("proxy_nav")
    assert "senha" not in bruto and "br.proxy" not in bruto  # cifrado
    r = await rig4.client.get("/v1/app/proxy-navegador", headers={"Authorization": "Bearer " + key})
    assert r.status_code == 200
    j = r.json()
    assert j["proxy"] == "http://user:senha@br.proxy.exemplo:8000"
    assert "instagram.com" in j["dominios"]
    _, outra = await rig4.person("cli-sem-proxy")
    r = await rig4.client.get("/v1/app/proxy-navegador", headers={"Authorization": "Bearer " + outra})
    assert r.json() == {"proxy": None, "dominios": []}


async def test_padrao_vale_para_quem_nao_tem(rig4: Rig):
    ctx = rig4.app.state.ctx
    _, key = await rig4.person("cli-padrao")
    P.guardar(ctx, "padrao", "socks5://u:p@10.0.0.1:1080")
    r = await rig4.client.get("/v1/app/proxy-navegador", headers={"Authorization": "Bearer " + key})
    assert r.json()["proxy"].startswith("socks5://")


def test_valida_e_mascara():
    assert P.mascarar("http://u:s@h:1") == "http://***@h:1"
    with pytest.raises(ValueError):
        P.validar_url("h:1")
    with pytest.raises(ValueError):
        P.validar_url("ftp://h:1")
