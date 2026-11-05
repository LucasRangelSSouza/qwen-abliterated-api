"""Imagem pelo llm-access: autentica, repassa ao llm-queue com a chave de servico e o nome de quem pediu; o painel so enxerga com login."""
from tests.conftest import OWNER_KEY, UP_ADMIN, UP_KEY, Rig


async def test_imagem_exige_chave(rig: Rig):
    r = await rig.client.post("/v1/images/generations", json={"prompt": "x"})
    assert r.status_code == 401 and rig.up.ai_calls == 0


async def test_imagem_repassa_com_chave_de_servico_e_nome(rig: Rig):
    r = await rig.client.post("/v1/images/generations", json={"prompt": "hamburguer"}, headers={"Authorization": f"Bearer {OWNER_KEY}"})
    assert r.status_code == 200 and r.json()["data"][0]["b64_json"] == "AAAA"
    path, body, headers = rig.up.calls[-1]
    assert path == "/v1/images/generations" and body["prompt"] == "hamburguer"
    assert headers["authorization"] == f"Bearer {UP_KEY}" and headers["x-end-user"] == "dono"


async def test_imagem_nao_aceita_corpo_invalido(rig: Rig):
    r = await rig.client.post("/v1/images/generations", content=b"nao-json", headers={"Authorization": f"Bearer {OWNER_KEY}", "Content-Type": "application/json"})
    assert r.status_code == 400


async def test_status_e_imagem_do_pedido(rig: Rig):
    h = {"Authorization": f"Bearer {OWNER_KEY}"}
    assert (await rig.client.get("/v1/jobs/j1", headers=h)).json()["state"] == "done"
    r = await rig.client.get("/v1/jobs/j1/image", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"PNG")
    assert (await rig.client.get("/v1/jobs/j1")).status_code == 401


async def test_progresso_de_midia_passa_pelo_proxy(rig: Rig):
    h = {"Authorization": f"Bearer {OWNER_KEY}"}
    r = await rig.client.get("/v1/media/jobs/j1", headers=h)
    assert r.status_code == 200 and r.json()["progress"] == 40 and r.json()["status"] == "running"
    assert (await rig.client.get("/v1/media/jobs/j1")).status_code == 401


async def test_painel_de_imagem_exige_login(rig: Rig):
    for p in ("/media/status", "/media/jobs", "/media/image/j1"):
        assert (await rig.client.get("/admin/api" + p)).status_code == 401


async def test_painel_de_imagem_le_do_queue(rig: Rig):
    await rig.login()
    assert (await rig.client.get("/admin/api/media/status")).json()["instance"]["dph"] == 0.128
    assert (await rig.client.get("/admin/api/media/jobs")).json()["recent"][0]["id"] == "j1"
    r = await rig.client.get("/admin/api/media/image/j1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"PNG")


async def test_faturamento_e_metricas_exigem_login(rig: Rig):
    assert (await rig.client.get("/admin/api/billing")).status_code == 401
    assert (await rig.client.get("/admin/api/metrics")).status_code == 401


async def test_faturamento_e_metricas_leem_do_queue(rig: Rig):
    await rig.login()
    b = (await rig.client.get("/admin/api/billing")).json()
    assert b["credit_usd"] == 77.0 and b["machines"]["image"]["id"] == 2
    m = (await rig.client.get("/admin/api/metrics?machine=image")).json()
    assert m["machine"] == "image" and m["totals"]["requests"] == 3
    assert (await rig.client.get("/admin/api/metrics?machine=lixo")).json()["machine"] == "llm"  # maquina invalida cai em llm
