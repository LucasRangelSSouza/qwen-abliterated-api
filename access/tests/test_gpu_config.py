"""Configuracao da GPU no painel: o llm-access so repassa ao llm-queue (/admin/gpu/config), valida a entrada e nao guarda copia."""
import httpx
import pytest

from tests.conftest import Rig


async def test_gpu_config_exige_login(rig: Rig):
    assert (await rig.client.get("/admin/api/gpu-config")).status_code == 401
    assert (await rig.client.put("/admin/api/gpu-config", json={"reserved": True})).status_code == 401
    assert rig.up.config_calls == []


async def test_gpu_config_le_do_queue(rig: Rig):
    await rig.login()
    r = await rig.client.get("/admin/api/gpu-config")
    assert r.status_code == 200 and r.json()["auto_shutdown"] is False and r.json()["idle_min"] == 30.0


async def test_gpu_config_repassa_so_o_que_foi_enviado(rig: Rig):
    await rig.login()
    r = await rig.client.put("/admin/api/gpu-config", json={"auto_shutdown": True, "idle_min": 45})
    assert r.status_code == 200 and r.json()["auto_shutdown"] is True and r.json()["idle_min"] == 45
    assert rig.up.config_calls == [{"auto_shutdown": True, "idle_min": 45}]
    r = await rig.client.put("/admin/api/gpu-config", json={"reserved": True})
    assert r.json()["reserved"] is True and rig.up.config_calls[-1] == {"reserved": True}
    assert (await rig.client.get("/admin/api/gpu-config")).json()["auto_shutdown"] is True  # o estado e o do queue, nao do SQLite
    assert rig.app.state.ctx.store.query("SELECT * FROM settings") == []  # nenhuma copia no SQLite


@pytest.mark.parametrize("body", [{"idle_min": 0}, {"idle_min": 1441}, {"idle_min": "30"}, {"idle_min": True}, {"auto_shutdown": "sim"},
                                  {"reserved": 1}, {"outro": 1}, {}])
async def test_gpu_config_valida_antes_de_repassar(rig: Rig, body):
    await rig.login()
    r = await rig.client.put("/admin/api/gpu-config", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_gpu_config"
    assert rig.up.config_calls == []


async def test_gpu_config_limites_do_idle_min_aceitos(rig: Rig):
    await rig.login()
    for v in (1, 1440, 12.5):
        assert (await rig.client.put("/admin/api/gpu-config", json={"idle_min": v})).status_code == 200


async def test_gpu_config_json_invalido(rig: Rig):
    await rig.login()
    r = await rig.client.put("/admin/api/gpu-config", content=b"nao-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


async def test_gpu_config_queue_fora_do_ar_vira_502(rig: Rig):
    await rig.login()
    rig.up.config_status = 500
    assert (await rig.client.get("/admin/api/gpu-config")).status_code == 502
    r = await rig.client.put("/admin/api/gpu-config", json={"reserved": True})
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"


async def test_gpu_config_recusa_do_queue_chega_como_422(rig: Rig):
    await rig.login()
    rig.up.config_status = 422
    r = await rig.client.put("/admin/api/gpu-config", json={"idle_min": 10})
    assert r.status_code == 422 and "idle_min" in r.json()["error"]["message"]


async def test_gpu_config_usa_a_chave_de_admin_do_queue(make_rig):
    rig = await make_rig(upstream_admin_key="outra-chave")
    await rig.login()
    assert (await rig.client.get("/admin/api/gpu-config")).status_code == 502  # o fake so aceita a chave certa: 403 vira 502, sem vazar nada
