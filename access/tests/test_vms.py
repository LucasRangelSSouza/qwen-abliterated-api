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
        if m == "POST" and p == "/v1/secrets":
            return httpx.Response(201, json={"data": [{"secretId": 77}]})
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
                  (aid, "light", status, agora - dias_atraso * DIA, agora))
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


# ---------------------------------------------------------------- produto "maquina virtual" vindo da Cakto

SEG = "seg-cakto-teste"


async def cakto(r, evento, email="ana@x.com", produto="Máquina virtual Segundo Cérebro", sub="vm-sub-1", ev="e1", proximo="2026-12-04T10:00:00Z"):
    corpo = {"event": evento, "data": {"id": ev, "customer": {"name": "Ana", "email": email}, "product": {"name": produto}, "amount": 399.0,
                                       "subscription": {"id": sub, "next_payment_date": proximo}}}
    return await r.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers={"content-type": "application/json", "x-webhook-secret": SEG})


@pytest.fixture
async def rig2(make_rig):
    r = await make_rig(secrets_key="chave-mestra-de-teste", cakto_webhook_secret=SEG)
    ctx = r.app.state.ctx
    fake = FakeContabo()
    ctx.contabo_transport = httpx.MockTransport(fake.handler)
    V.salvar_credenciais(ctx, CRED)
    ctx.store.set_setting("vm_product_id", "V-BARATO")
    return r, ctx, fake


async def test_compra_da_vm_abre_pedido_sem_mexer_no_plano_e_renovacao_nao_duplica(rig2):
    r, ctx, fake = rig2
    assert (await cakto(r, "purchase_approved", produto="Segundo Cérebro Max", sub="plano-1", ev="p1")).status_code == 200  # plano primeiro
    plano_antes = dict(ctx.store.one("SELECT plan,status FROM subscriptions"))
    assert (await cakto(r, "purchase_approved")).status_code == 200  # a VM (order bump)
    vms = ctx.store.query("SELECT * FROM vms")
    assert len(vms) == 1 and vms[0]["status"] == "requested" and vms[0]["billing_status"] == "active" and vms[0]["external_id"] == "vm-sub-1"
    assert dict(ctx.store.one("SELECT plan,status FROM subscriptions")) == plano_antes  # o plano da conta nao mudou
    assert ctx.store.one("SELECT COUNT(*) n FROM subscriptions")["n"] == 1  # a VM nao virou assinatura de plano
    await cakto(r, "subscription_renewed", ev="e2")  # renovacao da VM
    assert ctx.store.one("SELECT COUNT(*) n FROM vms")["n"] == 1
    assert fake.mutacoes() == []  # nada foi criado na Contabo (auto_provision desligado)


async def test_atraso_da_vm_desliga_mesmo_com_plano_em_dia(rig2):
    r, ctx, fake = rig2
    await cakto(r, "purchase_approved", produto="Segundo Cérebro Max", sub="plano-1", ev="p1")
    await cakto(r, "purchase_approved")
    vm_id = ctx.store.one("SELECT id FROM vms")["id"]
    await V.provisionar(ctx, vm_id)
    await V.sincronizar(ctx)
    await cakto(r, "subscription_late", ev="e3", proximo="2000-01-01T00:00:00Z")  # VM vencida ha muito tempo; plano segue ativo
    assert ctx.store.one("SELECT status FROM subscriptions")["status"] == "active"
    assert (await V.varredura(ctx))["suspensas"] == 1
    await cakto(r, "purchase_approved", ev="e4", proximo="2099-01-01T00:00:00Z")  # pagou a VM
    assert (await V.varredura(ctx))["religadas"] == 1


async def test_auto_provision_so_cria_vm_paga_e_so_se_ligado(rig2):
    r, ctx, fake = rig2
    await cakto(r, "purchase_approved")
    assert await V.provisionar_pagas(ctx) == 0 and fake.mutacoes() == []  # desligado por padrao
    ctx.store.set_setting("vm_auto_provision", "1")
    assert await V.provisionar_pagas(ctx) == 1
    assert [p for m, p in fake.mutacoes() if p == "/v1/compute/instances"]
    assert ctx.store.one("SELECT status FROM vms")["status"] == "provisioning"
    assert await V.provisionar_pagas(ctx) == 0  # nao cria de novo


async def test_pedido_pendente_de_pagamento_nao_cria_vm(rig2):
    r, ctx, fake = rig2
    ctx.store.set_setting("vm_auto_provision", "1")
    await cakto(r, "purchase_created")  # pendente: so um boleto/pix aguardando
    assert ctx.store.one("SELECT COUNT(*) n FROM vms")["n"] == 0
    assert await V.provisionar_pagas(ctx) == 0


async def test_chave_ssh_do_dono_vai_nas_vms_novas(rig):
    r, ctx, fake = rig
    await r.login()
    sid = (await r.client.post("/admin/api/vms/ssh-key", json={"public_key": "ssh-ed25519 AAAAteste dono@x"})).json()["secret_id"]
    assert sid == 77 and (await r.client.get("/admin/api/vms/config")).json()["ssh_secret_id"] == "77"
    assert (await r.client.post("/admin/api/vms/ssh-key", json={"public_key": "isto nao e chave"})).status_code == 422
    aid, _ = conta(ctx)
    await V.provisionar(ctx, V.solicitar(ctx, aid))
    corpo = [b for m, p, b in fake.calls if m == "POST" and p == "/v1/compute/instances"][0]
    assert corpo["sshKeys"] == [77]


def test_toda_tabela_com_id_autoincremento_devolve_lastrowid_no_postgres():
    """O adaptador do Postgres so devolve o id das tabelas da lista; a lista sai do schema, entao tabela nova (ex.: vms) nunca fica de fora."""
    from app.store import SCHEMA
    from app.store_pg import TABLES_WITH_ID
    import re
    todas = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\(\s*id INTEGER PRIMARY KEY AUTOINCREMENT", SCHEMA)
    assert "vms" in TABLES_WITH_ID and set(todas) == set(TABLES_WITH_ID) and len(todas) >= 8


async def test_excluir_assinante_remove_conta_revoga_chaves_e_protege_vm_viva(rig):
    r, ctx, fake = rig
    await r.login()
    aid, pid = conta(ctx, email="apagar@x.com")
    chave = V._chave_dedicada(ctx, pid, "teste")
    vm_id = V.solicitar(ctx, aid)
    await V.provisionar(ctx, vm_id)  # VM viva na Contabo (simulada)
    resp = await r.client.delete(f"/admin/api/subscribers/{aid}")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "vm_active"
    assert ctx.store.one("SELECT id FROM accounts WHERE id=?", (aid,))  # nada foi apagado
    await V.cancelar(ctx, vm_id)
    assert (await r.client.delete(f"/admin/api/subscribers/{aid}")).status_code == 200
    assert ctx.store.one("SELECT id FROM accounts WHERE id=?", (aid,)) is None
    assert ctx.store.one("SELECT COUNT(*) n FROM vms WHERE account_id=?", (aid,))["n"] == 0
    assert ctx.store.one("SELECT revoked_at FROM keys WHERE key_hash=?", (sha256(chave),))["revoked_at"] is not None
    assert ctx.store.one("SELECT status FROM people WHERE id=?", (pid,))["status"] == "suspended"
    assert (await r.client.delete(f"/admin/api/subscribers/{aid}")).status_code == 404


async def test_webhook_v2_com_plano_e_order_bump_no_mesmo_evento_cria_plano_e_pedido_de_vm(rig2):
    r, ctx, fake = rig2
    def pedido(produto, ev, sub, tipo):
        return {"id": ev, "offer_type": tipo, "customer": {"name": "Ana", "email": "bump@x.com"}, "product": {"name": produto}, "amount": 399.0, "subscription": {"id": sub}}
    corpo = {"event": "purchase_approved", "data": [pedido("Segundo Cérebro Max", "p1", "plano-1", "main"), pedido("Máquina virtual Segundo Cérebro", "v1", "vm-1", "orderbump")]}
    resp = await r.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers={"content-type": "application/json", "x-webhook-secret": SEG})
    assert resp.status_code == 200 and resp.json()["processed"] == 2
    assert ctx.store.one("SELECT plan,status FROM subscriptions")["status"] == "active" and ctx.store.one("SELECT COUNT(*) n FROM subscriptions")["n"] == 1
    vms = ctx.store.query("SELECT * FROM vms")
    assert len(vms) == 1 and vms[0]["status"] == "requested" and vms[0]["external_id"] == "vm-1"
    again = await r.client.post("/v1/billing/cakto", content=json.dumps(corpo), headers={"content-type": "application/json", "x-webhook-secret": SEG})
    assert again.json()["duplicate"] is True and ctx.store.one("SELECT COUNT(*) n FROM vms")["n"] == 1


async def test_produto_combinado_plano_mais_vm_aplica_o_plano_e_abre_o_pedido_da_vm(rig2):
    r, ctx, fake = rig2
    ctx.store.set_setting("billing_auto_enable", "1")  # como em producao: quem paga entra liberado
    assert (await cakto(r, "purchase_approved", email="combo@x.com", produto="Segundo Cérebro Max + Máquina virtual", sub="combo-1", ev="c1")).status_code == 200
    sub = ctx.store.one("SELECT plan,status FROM subscriptions")
    assert sub["plan"] == "max" and sub["status"] == "active"
    pessoa = ctx.store.one("SELECT p.status FROM people p JOIN accounts a ON a.person_id=p.id WHERE a.email='combo@x.com'")
    assert pessoa["status"] == "active"
    vms = ctx.store.query("SELECT * FROM vms")
    assert len(vms) == 1 and vms[0]["status"] == "requested" and vms[0]["external_id"] == "combo-1"
    assert (await cakto(r, "subscription_renewed", email="combo@x.com", produto="Segundo Cérebro Max + Máquina virtual", sub="combo-1", ev="c2")).status_code == 200
    assert ctx.store.one("SELECT COUNT(*) n FROM vms")["n"] == 1 and ctx.store.one("SELECT COUNT(*) n FROM subscriptions")["n"] == 1


async def test_combinado_cancelado_bloqueia_o_plano_e_marca_a_vm(rig2):
    r, ctx, fake = rig2
    await cakto(r, "purchase_approved", email="c2@x.com", produto="Segundo Cérebro Pro + Máquina virtual", sub="combo-9", ev="d1")
    await cakto(r, "subscription_canceled", email="c2@x.com", produto="Segundo Cérebro Pro + Máquina virtual", sub="combo-9", ev="d2")
    assert ctx.store.one("SELECT status FROM subscriptions")["status"] == "canceled"
    assert ctx.store.one("SELECT billing_status FROM vms")["billing_status"] == "canceled"


async def test_planos_anuais_sao_identificados_pelo_nome_e_nao_quebram_plan_from_hint(rig2):
    from app import accounts as A
    r, ctx, fake = rig2
    for nome, esperado in (("Segundo Cérebro Light anual", "light"), ("Segundo Cérebro Pro anual", "light"), ("Segundo Cérebro Max anual + Máquina virtual", "max"), ("Segundo Cérebro Ultra anual", "ultra"),
                           ("Segundo Cérebro Infinity anual + Máquina virtual", "infinity"), ("Máquina virtual Segundo Cérebro anual", None)):
        assert A.plan_from_hint(ctx, nome.lower()) == esperado, nome
    assert V.eh_produto_vm(ctx, "máquina virtual segundo cérebro anual") and V.eh_produto_vm(ctx, "segundo cérebro max anual + máquina virtual")
    assert not V.eh_produto_vm(ctx, "segundo cérebro max anual")


async def test_assinatura_anual_combinada_fica_ativa_o_ano_todo_e_so_atrasa_depois_do_vencimento(rig2):
    from app import accounts as A
    r, ctx, fake = rig2
    ctx.store.set_setting("billing_auto_enable", "1")
    ano = ctx.clock() + 365 * 86400
    iso = __import__("datetime").datetime.fromtimestamp(ano, __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (await cakto(r, "purchase_approved", email="anual@x.com", produto="Segundo Cérebro Max anual + Máquina virtual", sub="anual-1", ev="a1", proximo=iso)).status_code == 200
    sub = ctx.store.one("SELECT * FROM subscriptions")
    assert sub["plan"] == "max" and sub["status"] == "active" and abs(sub["next_payment_at"] - ano) < 2
    vm = ctx.store.one("SELECT * FROM vms")
    assert vm["status"] == "requested" and vm["billing_status"] == "active" and abs(vm["next_payment_at"] - ano) < 2
    assert A.sub_allows_use(ctx, sub) and V.deve_ficar_desligada(ctx, V._cobranca(vm, sub)) is False  # a seis meses de vencer, nada e cortado
    # venceu e nao pagou: atrasa, ainda dentro da carencia; passada a carencia, desliga
    assert (await cakto(r, "subscription_late", email="anual@x.com", produto="Segundo Cérebro Max anual + Máquina virtual", sub="anual-1", ev="a2", proximo="2020-01-01T00:00:00Z")).status_code == 200
    vm = ctx.store.one("SELECT * FROM vms")
    assert vm["billing_status"] == "late" and V.deve_ficar_desligada(ctx, V._cobranca(vm, ctx.store.one("SELECT * FROM subscriptions"))) is True  # 2020 esta alem da carencia

async def test_pagamento_do_combo_adota_a_vm_que_ja_existe_sem_comprar_outra(rig2):
    """Cliente que ja tem conta e VM rodando (criadas antes do pagamento, sem vinculo de cobranca) paga o combo: a VM atual e adotada."""
    r, ctx, fake = rig2
    aid, pid = conta(ctx, email="juca@x.com")
    with ctx.store.tx() as c:
        c.execute("INSERT INTO vms(account_id,name,contabo_id,status,product_id,region,ip,cost_cents,price_cents,created_at,updated_at,external_id,billing_status)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,'active')", (aid, "sc-vm-existente", "203728244", "running", "V153", "EU", "13.140.216.191", 3300, 39900, ctx.clock(), ctx.clock()))
    antes = ctx.store.one("SELECT id FROM vms")["id"]
    assert (await cakto(r, "purchase_approved", email="juca@x.com", produto="Segundo Cérebro Lite + Máquina virtual", sub="combo-juca", ev="j1")).status_code == 200
    vms = ctx.store.query("SELECT * FROM vms")
    assert len(vms) == 1 and vms[0]["id"] == antes  # nenhuma VM nova
    assert vms[0]["external_id"] == "combo-juca" and vms[0]["billing_status"] == "active" and vms[0]["status"] == "running" and vms[0]["contabo_id"] == "203728244"
    assert fake.mutacoes() == []  # nada foi comprado nem alterado na Contabo
    assert ctx.store.one("SELECT plan,status FROM subscriptions WHERE external_id='combo-juca'")["plan"] == "light"
    assert len(ctx.store.query("SELECT id FROM accounts")) == 1  # a conta existente foi reaproveitada
    # renovacao do mes seguinte: mesmo id de assinatura, continua a mesma VM
    assert (await cakto(r, "purchase_approved", email="juca@x.com", produto="Segundo Cérebro Lite + Máquina virtual", sub="combo-juca", ev="j2", proximo="2027-01-04T10:00:00Z")).status_code == 200
    assert len(ctx.store.query("SELECT id FROM vms")) == 1 and fake.mutacoes() == []

async def test_evento_de_assinatura_criada_logo_apos_a_aprovacao_nao_bloqueia_o_cliente(rig2):
    """A Cakto manda purchase_approved e, 200 ms depois, subscription_created: o segundo nao pode deixar a assinatura e a VM como pending."""
    r, ctx, fake = rig2
    from app import accounts as A
    assert (await cakto(r, "purchase_approved", email="novo@x.com", produto="Segundo Cérebro Lite + Máquina virtual", sub="s-1", ev="n1")).status_code == 200
    assert (await cakto(r, "subscription_created", email="novo@x.com", produto="Segundo Cérebro Lite + Máquina virtual", sub="s-1", ev="n2")).status_code == 200
    sub = ctx.store.one("SELECT * FROM subscriptions WHERE external_id='s-1'")
    assert sub["status"] == "active" and A.sub_allows_use(ctx, sub)
    assert ctx.store.one("SELECT billing_status FROM vms")["billing_status"] == "active"
