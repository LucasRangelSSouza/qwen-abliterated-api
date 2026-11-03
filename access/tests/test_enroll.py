"""Auto-cadastro (/v1/enroll): pessoa nasce suspensa, codigo de placa, limite por IP, idempotencia."""
import re

from tests.conftest import Rig

PLACA = re.compile(r"^[A-Z]{3}\d[A-Z]\d{2}$")
CORPO = {"machine_id": "abcdef0123456789", "app": "segundo-cerebro", "version": "0.3.0", "host": "NOTE-DO-LUCAS", "os": "win32"}


async def test_nasce_suspensa_com_codigo_e_nao_consome(rig: Rig):
    r = await rig.client.post("/v1/enroll", json=CORPO)
    assert r.status_code == 200, r.text
    j = r.json()
    assert PLACA.match(j["code"]) and j["status"] == "suspended" and j["api_key"].startswith("sk-")
    chat = await rig.chat(j["api_key"])
    assert chat.status_code == 403 and chat.json()["error"]["code"] == "account_suspended"
    await rig.login()
    achada = (await rig.client.get("/admin/api/people", params={"q": j["code"]})).json()
    assert len(achada) == 1 and achada[0]["status"] == "suspended" and "NOTE-DO-LUCAS" in achada[0]["note"]
    assert j["api_key"] not in str(achada)


async def test_liberar_pelo_painel_ativa_a_mesma_chave(rig: Rig):
    j = (await rig.client.post("/v1/enroll", json=CORPO)).json()
    await rig.login()
    pid = (await rig.client.get("/admin/api/people", params={"q": j["code"]})).json()[0]["id"]
    assert (await rig.client.post(f"/admin/api/people/{pid}/resume")).status_code == 200
    assert (await rig.chat(j["api_key"])).status_code == 200


async def test_mesma_maquina_nao_duplica_nem_reemite_chave(rig: Rig):
    a = (await rig.client.post("/v1/enroll", json=CORPO)).json()
    b = (await rig.client.post("/v1/enroll", json=CORPO)).json()
    assert b["code"] == a["code"] and b["api_key"] is None
    await rig.login()
    assert len((await rig.client.get("/admin/api/people")).json()) == 1


async def test_validacao_e_limite_por_ip(rig: Rig):
    assert (await rig.client.post("/v1/enroll", json={"machine_id": "x"})).status_code == 422
    for i in range(4):  # a tentativa invalida acima ja gastou 1 das 5
        assert (await rig.client.post("/v1/enroll", json={**CORPO, "machine_id": f"maquina-numero-{i}"})).status_code == 200
    r = await rig.client.post("/v1/enroll", json={**CORPO, "machine_id": "maquina-numero-99"})
    assert r.status_code == 429
    rig.clock.advance(3601)
    assert (await rig.client.post("/v1/enroll", json={**CORPO, "machine_id": "maquina-numero-100"})).status_code == 200
