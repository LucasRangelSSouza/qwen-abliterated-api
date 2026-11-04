"""VMs de clientes na Contabo: credenciais cifradas, travas de seguranca, provisionamento e desligar/religar por pagamento.
A Contabo e simulada (httpx.MockTransport): nenhum teste cria ou toca VPS de verdade."""
import json

import httpx
import pytest

from app import vms as V
from app.auth import sha256

pytestmark = pytest.mark.asyncio

CRED = {"client_id": "INT-1", "client_secret": "segredo-da-contabo", "api_user": "dono@x.com", "api_password": "senha-api"}
DIA = 86400


class FakeContabo:
    def __init__(self):
        self.instances = {
            "203468840": {"instanceId": 203468840, "displayName": "vmi3468840", "status": "running"},  # VPS de producao
            "555": {"instanceId": 555, "displayName": "minha-outra-vps", "status": "running"},
        }
        self.calls = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        if "auth.contabo.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        self.calls.append((m, p, json.loads(req.content) if req.content else None))
        if m == "GET" and p == "/v1/compute/instances":
            return httpx.Response(200, json={"data": list(self.instances.values()), "_pagination": {"totalPages": 1}})
        if m == "GET" and p.startswith("/v1/compute/instances/"):
            i = self.instances.get(p.rsplit("/", 1)[1])
            return httpx.Response(200 if i else 404, json={"data": [i] if i else []})
        if m == "POST" and p == "/v1/compute/instances":
            body = json.loads(req.content)
            novo = {"instanceId": 900 + len(self.instances), "displayName": body["displayName"], "status": "running", "cpuCores": 4, "ramMb": 8192,
                    "diskMb": 76800, "ipConfig": {"v4": {"ip": "203.0.113.9"}}}
            self.instances[str(novo["instanceId"])] = novo
            return httpx.Response(201, json={"data": [novo]})
        if m == "POST":
            return httpx.Response(200, json={"data": [{}]})
        return httpx.Response(404, json={})

    def mutacoes(self):
        return [(m, p) for m, p, _ in self.calls if m == "POST"]


@pytest.fixture
async def rig(make_rig):
    r = await make_rig(secrets_key="chave-mestra-de-teste")
    ctx = r.app.state.ctx
    fake = FakeContabo()
    ctx.contabo_transport = httpx.MockTransport(fake.handler)
    V.salvar_credenciais(ctx, CRED)
    ctx.store.set_setting("vm_product_id", "V-BARATO")
    return r, ctx, fake


def conta(ctx, email="ana@x.com", status="active", dias_atraso=0):
    agora = ctx.clock()
    with ctx.store.tx() as c:
        pid = c.execute("INSERT INTO people(name,note,created_at) VALUES('Ana','',?)", (agora,)).lastrowid
        aid = c.execute("INSERT INTO accounts(email,name,pw_hash,person_id,created_at) VALUES(?,?,?,?,?)", (email, "Ana", "x", pid, agora)).lastrowid
        c.execute("INSERT INTO subscriptions(account_id,plan,status,next_payment_at,updated_at) VALUES(?,?,?,?,?)",
                  (aid, "pro", status, agora - dias_atraso * DIA, agora))
    return aid, pid


async def test_credenciais_ficam_cifradas_e_exigem_chave_mestra(make_rig):
    r = await make_rig(secrets_key="chave-mestra-de-teste")
    ctx = r.app.state.ctx
    V.salvar_credenciais(ctx, CRED)
    guardado = ctx.store.get_setting("contabo_credentials")
    assert "segredo-da-contabo" not in guardado and "senha-api" not in guardado
    assert V.carregar_credenciais(ctx) == CRED
    ctx.s.secrets_key = "outra-chave"
    with pytest.raises(V.ContaboError):
        V.carregar_credenciais(ctx)
    ctx.s.secrets_key = ""
    with pytest.raises(V.ContaboError):
        V.salvar_credenciais(ctx, CRED)


async def test_trava_nunca_mexe_na_vps_de_producao_nem_em_instancia_alheia(rig):
    _r, ctx, fake = rig
    c = V.cliente(ctx)
    for alvo in ("203468840", "555"):
        for chamada in (c.acao(alvo, "shutdown"), c.cancelar(alvo)):
            with pytest.raises(V.ContaboError):
                await chamada
    assert fake.mutacoes() == []  # nenhuma chamada que altera algo chegou na Contabo
    with pytest.raises(V.ContaboError):
        await c.criar(nome="vps-qualquer", produto="V1", imagem="img", regiao="EU", user_data="")
    await c.aclose()


async def test_provisionar_cria_com_prefixo_chave_dedicada_e_instalador(rig):
    _r, ctx, fake = rig
    aid, pid = conta(ctx)
    vm_id = V.solicitar(ctx, aid)
    vm = await V.provisionar(ctx, vm_id)
    assert vm["status"] == "provisioning" and vm["contabo_id"] and vm["name"].startswith("sc-vm-")
    corpo = [b for m, p, b in fake.calls if m == "POST" and p == "/v1/compute/instances"][0]
    assert corpo["displayName"] == vm["name"] and corpo["productId"] == "V-BARATO" and corpo["imageId"] == V.IMAGEM_UBUNTU_2404
    assert "instalar-servidor.sh" in corpo["userData"] and "SB_CONTA_EMAIL='ana@x.com'" in corpo["userData"]
    chave = corpo["userData"].split("SB_CONTA_CHAVE='")[1].split("'")[0]
    k = ctx.store.one("SELECT label,revoked_at FROM keys WHERE key_hash=?", (sha256(chave),))
    assert k["label"] == "vm:" + vm["name"] and k["revoked_at"] is None  # chave propria, rotulo vm: (nao conta como computador)
    with pytest.raises(V.ContaboError):  # nao cria duas vezes
        await V.provisionar(ctx, vm_id)
    assert await V.sincronizar(ctx) == 1
    v = ctx.store.one("SELECT * FROM vms WHERE id=?", (vm_id,))
    assert v["status"] == "running" and v["ip"] == "203.0.113.9" and v["ram_mb"] == 8192


async def test_provisionar_sem_plano_configurado_falha_sem_criar(rig):
    _r, ctx, fake = rig
    ctx.store.set_setting("vm_product_id", "")
    aid, _ = conta(ctx)
    vm_id = V.solicitar(ctx, aid)
    with pytest.raises(V.ContaboError):
        await V.provisionar(ctx, vm_id)
    assert ctx.store.one("SELECT status,last_error FROM vms WHERE id=?", (vm_id,))["status"] == "error"
    assert fake.mutacoes() == []


async def test_desliga_apos_a_carencia_e_religa_quando_paga(rig):
    _r, ctx, fake = rig
    aid, _ = conta(ctx, status="late", dias_atraso=10)  # atrasada, mas dentro dos 15 dias
    vm_id = V.solicitar(ctx, aid)
    await V.provisionar(ctx, vm_id)
    await V.sincronizar(ctx)
    assert (await V.varredura(ctx))["suspensas"] == 0
    with ctx.store.tx() as c:  # passa dos 15 dias
        c.execute("UPDATE subscriptions SET next_payment_at=? WHERE account_id=?", (ctx.clock() - 16 * DIA, aid))
    assert (await V.varredura(ctx))["suspensas"] == 1
    assert ctx.store.one("SELECT status FROM vms WHERE id=?", (vm_id,))["status"] == "suspended"
    assert any(p.endswith("/actions/shutdown") for _m, p in fake.mutacoes())
    assert (await V.varredura(ctx))["suspensas"] == 0  # idempotente
    with ctx.store.tx() as c:  # pagou
        c.execute("UPDATE subscriptions SET status='active' WHERE account_id=?", (aid,))
    assert (await V.varredura(ctx))["religadas"] == 1
    assert ctx.store.one("SELECT status FROM vms WHERE id=?", (vm_id,))["status"] == "running"
    assert any(p.endswith("/actions/start") for _m, p in fake.mutacoes())


async def test_cancelamento_da_assinatura_desliga_na_hora(rig):
    _r, ctx, _fake = rig
    aid, _ = conta(ctx)
    vm_id = V.solicitar(ctx, aid)
    await V.provisionar(ctx, vm_id)
    await V.sincronizar(ctx)
    with ctx.store.tx() as c:
        c.execute("UPDATE subscriptions SET status='canceled' WHERE account_id=?", (aid,))
    assert (await V.varredura(ctx))["suspensas"] == 1


async def test_painel_lista_configura_e_nunca_devolve_credenciais(rig):
    r, ctx, fake = rig
    await r.login()
    aid, _ = conta(ctx)
    cfg = (await r.client.put("/admin/api/vms/config", json={"cost_cents": 3000, "price_cents": 39900, "grace_days": 15, "region": "EU",
                                                              "credentials": CRED})).json()
    assert cfg["credentials_set"] and cfg["cost_cents"] == 3000 and "client_secret" not in json.dumps(cfg) and "senha-api" not in json.dumps(cfg)
    assert (await r.client.put("/admin/api/vms/config", json={"cost_cents": -1})).status_code == 422
    vm_id = (await r.client.post("/admin/api/vms", json={"account_id": aid})).json()["id"]
    assert (await r.client.post(f"/admin/api/vms/{vm_id}/provision")).status_code == 200
    lista = (await r.client.get("/admin/api/vms")).json()
    assert lista["rows"][0]["status"] == "provisioning" and lista["rows"][0]["margin_cents"] == 39900 - 3000
    assert lista["total"] == {"vms": 1, "cost_cents": 3000, "price_cents": 39900}
    sync = (await r.client.post("/admin/api/vms/sync")).json()
    assert sync["atualizadas"] == 1
    assert (await r.client.post("/admin/api/vms/99999/suspend")).status_code == 404


async def test_painel_exige_login(rig):
    r, _ctx, _fake = rig
    assert (await r.client.get("/admin/api/vms")).status_code == 401
    assert (await r.client.post("/admin/api/vms/1/provision")).status_code == 401
