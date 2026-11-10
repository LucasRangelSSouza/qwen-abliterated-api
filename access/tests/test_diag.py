"""Relatorio de falha de instalacao: anonimo, com limite, so o admin le."""
from app import diag
from tests.conftest import Rig


async def test_recebe_e_o_admin_le(rig: Rig):
    r = await rig.client.post("/v1/diag/instalacao", json={"versao": "0.6.52", "so": "win32", "etapa": "baixando o instalador", "codigo": 1, "log": "linha 1\nlinha 2"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (await rig.client.get("/admin/api/diag")).status_code == 401  # sem login do admin nao le
    await rig.login()
    j = (await rig.client.get("/admin/api/diag")).json()["relatorios"]
    assert len(j) == 1 and j[0]["versao"] == "0.6.52" and j[0]["etapa"] == "baixando o instalador" and "linha 2" in j[0]["log"]


async def test_limita_tamanho_e_quantidade(rig: Rig):
    await rig.client.post("/v1/diag/instalacao", json={"log": "x" * 50000})
    await rig.login()
    j = (await rig.client.get("/admin/api/diag")).json()["relatorios"]
    assert len(j[0]["log"]) == diag.MAX_LOG
    for _ in range(diag.POR_HORA):
        r = await rig.client.post("/v1/diag/instalacao", json={"log": "a"})
    assert r.status_code == 429  # o 21o na mesma hora e recusado
    rig.clock.advance(3601)
    assert (await rig.client.post("/v1/diag/instalacao", json={"log": "a"})).status_code == 200
