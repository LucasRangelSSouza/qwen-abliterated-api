"""Maquinas virtuais dedicadas dos clientes (Segundo Cerebro em modo servidor, 1 VPS por cliente na Contabo).

Regras de seguranca (a automacao NUNCA pode derrubar a infraestrutura propria):
- so cria, liga, desliga ou cancela instancias cujo `displayName` comeca com `sc-vm-`;
- a VPS de producao (ids em PROTEGIDAS) e recusada antes de qualquer chamada que altere algo;
- criar uma VPS custa dinheiro: so acontece pela acao explicita do painel (`/admin/api/vms/{id}/provision`) ou, se o dono
  ligar `vm_auto_provision`, quando o pagamento da VM e confirmado. Sem credenciais da Contabo configuradas, nada roda.
As credenciais da Contabo ficam cifradas (Fernet) em `settings`; a chave mestra vem do ambiente (SECRETS_KEY), nunca do banco.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.accounts import current_sub
from app.auth import new_api_key, sha256
from app.ctx import Ctx

PREFIXO = "sc-vm-"
PROTEGIDAS = {"203468840"}  # VPS de producao (66.94.101.153): nunca tocar
AUTH_URL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
API = "https://api.contabo.com/v1/compute"
IMAGEM_UBUNTU_2404 = "d64d5c6c-9dda-4e38-8174-0ee282474d8a"
INSTALADOR = "https://raw.githubusercontent.com/RangelTech/second-brain/main/scripts/instalar-servidor.sh"
STATUS = ("requested", "provisioning", "running", "suspended", "canceled", "error")
CARENCIA_PADRAO_DIAS = 15  # a Contabo tolera ~20 dias de atraso; desligamos antes para nao tomar a frente


class ContaboError(Exception):
    pass


# ---------------------------------------------------------------- credenciais cifradas

def _fernet(ctx: Ctx) -> Fernet:
    seg = ctx.s.secrets_key
    if not seg:
        raise ContaboError("SECRETS_KEY nao configurada no servidor; nao da para guardar credenciais da Contabo.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seg.encode()).digest()))


def salvar_credenciais(ctx: Ctx, dados: dict) -> None:
    campos = ("client_id", "client_secret", "api_user", "api_password")
    if not all(str(dados.get(c) or "").strip() for c in campos):
        raise ContaboError("Informe client_id, client_secret, api_user e api_password.")
    corpo = json.dumps({c: str(dados[c]).strip() for c in campos}).encode()
    ctx.store.set_setting("contabo_credentials", _fernet(ctx).encrypt(corpo).decode())


def carregar_credenciais(ctx: Ctx) -> dict | None:
    v = ctx.store.get_setting("contabo_credentials")
    if not v:
        return None
    try:
        return json.loads(_fernet(ctx).decrypt(v.encode()))
    except (InvalidToken, ValueError):
        raise ContaboError("Nao consegui abrir as credenciais da Contabo (SECRETS_KEY mudou?).")


def credenciais_configuradas(ctx: Ctx) -> bool:
    return bool(ctx.store.get_setting("contabo_credentials"))


def config(ctx: Ctx) -> dict:
    g = ctx.store.get_setting
    return {
        "product_id": g("vm_product_id") or "",
        "image_id": g("vm_image_id") or IMAGEM_UBUNTU_2404,
        "region": g("vm_region") or "EU",
        "cost_cents": int(g("vm_cost_cents") or 0),     # o que PAGAMOS por mes na Contabo (informado pelo dono)
        "price_cents": int(g("vm_price_cents") or 39900),  # o que COBRAMOS do cliente por mes
        "grace_days": int(g("vm_grace_days") or CARENCIA_PADRAO_DIAS),
        "auto_provision": g("vm_auto_provision") == "1",
        "ssh_secret_id": g("vm_ssh_secret_id") or "",  # id (na Contabo) da chave SSH do dono, injetada nas VMs novas para suporte
    }


# ---------------------------------------------------------------- cliente Contabo

class Contabo:
    def __init__(self, creds: dict, transport: httpx.AsyncBaseTransport | None = None):
        self.creds = creds
        self.http = httpx.AsyncClient(timeout=30.0, transport=transport)
        self._tok: tuple[str, float] | None = None

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _token(self) -> str:
        if self._tok and self._tok[1] > time.time() + 30:
            return self._tok[0]
        r = await self.http.post(AUTH_URL, data={
            "client_id": self.creds["client_id"], "client_secret": self.creds["client_secret"],
            "username": self.creds["api_user"], "password": self.creds["api_password"], "grant_type": "password"})
        if r.status_code != 200:
            raise ContaboError(f"A Contabo recusou o login da API (HTTP {r.status_code}).")
        j = r.json()
        self._tok = (j["access_token"], time.time() + int(j.get("expires_in", 300)))
        return self._tok[0]

    async def _req(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        r = await self.http.request(metodo, API + caminho, json=corpo, headers={
            "Authorization": "Bearer " + await self._token(), "x-request-id": str(uuid.uuid4())})
        if r.status_code >= 300:
            raise ContaboError(f"Contabo {metodo} {caminho}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    async def listar(self) -> list[dict]:
        out, pagina = [], 1
        while True:
            j = await self._req("GET", f"/instances?size=100&page={pagina}")
            out += j.get("data", [])
            if pagina >= int((j.get("_pagination") or {}).get("totalPages", 1)):
                return out
            pagina += 1

    async def obter(self, instancia_id: str) -> dict:
        d = (await self._req("GET", f"/instances/{instancia_id}")).get("data") or []
        if not d:
            raise ContaboError(f"Instancia {instancia_id} nao encontrada.")
        return d[0]

    async def _nossa(self, instancia_id: str) -> dict:
        """Trava de seguranca: so mexe em instancia nossa (prefixo sc-vm-) e nunca na VPS de producao."""
        if str(instancia_id) in PROTEGIDAS:
            raise ContaboError("Instancia protegida: a automacao nao pode alterar a VPS de producao.")
        inst = await self.obter(instancia_id)
        if not str(inst.get("displayName") or "").startswith(PREFIXO):
            raise ContaboError(f"A instancia {instancia_id} nao e uma VM de cliente (nome sem '{PREFIXO}').")
        return inst

    async def criar_chave_ssh(self, nome: str, chave_publica: str) -> int:
        """Guarda uma chave SSH publica na Contabo (secrets) para ser usada em VMs novas. Nao altera nenhuma instancia."""
        r = await self.http.post("https://api.contabo.com/v1/secrets", json={"name": nome, "type": "ssh", "value": chave_publica.strip()},
                                 headers={"Authorization": "Bearer " + await self._token(), "x-request-id": str(uuid.uuid4())})
        if r.status_code >= 300:
            raise ContaboError(f"Contabo POST /secrets: HTTP {r.status_code} {r.text[:200]}")
        return int((r.json().get("data") or [{}])[0].get("secretId"))

    async def criar(self, *, nome: str, produto: str, imagem: str, regiao: str, user_data: str, ssh_keys: list[int] | None = None) -> dict:
        if not nome.startswith(PREFIXO):
            raise ContaboError(f"Nome da VM precisa comecar com '{PREFIXO}'.")
        if not produto:
            raise ContaboError("Defina o productId do plano da VM (vm_product_id) antes de criar.")
        corpo = {"imageId": imagem, "productId": produto, "region": regiao, "displayName": nome, "userData": user_data, "period": 1, "defaultUser": "root"}
        if ssh_keys:
            corpo["sshKeys"] = ssh_keys
        j = await self._req("POST", "/instances", corpo)
        return (j.get("data") or [{}])[0]

    async def acao(self, instancia_id: str, acao: str) -> None:
        if acao not in ("start", "shutdown", "stop", "restart"):
            raise ContaboError("Acao nao permitida.")
        await self._nossa(instancia_id)
        await self._req("POST", f"/instances/{instancia_id}/actions/{acao}")

    async def cancelar(self, instancia_id: str) -> None:
        await self._nossa(instancia_id)
        await self._req("POST", f"/instances/{instancia_id}/cancel")


def cliente(ctx: Ctx) -> Contabo:
    creds = carregar_credenciais(ctx)
    if not creds:
        raise ContaboError("Credenciais da Contabo nao configuradas.")
    return Contabo(creds, getattr(ctx, "contabo_transport", None))


# ---------------------------------------------------------------- ciclo de vida

def solicitar(ctx: Ctx, account_id: int) -> int:
    """Registra o pedido de uma VM para a conta (nao cria nada na Contabo)."""
    cfg = config(ctx)
    agora = ctx.clock()
    nome = f"{PREFIXO}{account_id}-{uuid.uuid4().hex[:6]}"
    with ctx.store.tx() as c:
        cur = c.execute("INSERT INTO vms(account_id,name,status,product_id,region,cost_cents,price_cents,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (account_id, nome, "requested", cfg["product_id"], cfg["region"], cfg["cost_cents"], cfg["price_cents"], agora, agora))
        return cur.lastrowid


def _mudar(ctx: Ctx, vm_id: int, **campos) -> None:
    campos["updated_at"] = ctx.clock()
    cols = ",".join(f"{k}=?" for k in campos)
    with ctx.store.tx() as c:
        c.execute(f"UPDATE vms SET {cols} WHERE id=?", (*campos.values(), vm_id))


def _user_data(email: str, chave: str) -> str:
    return ("#cloud-config\npackage_update: true\nruncmd:\n"
            f"  - [ bash, -c, \"curl -fsSL {INSTALADOR} | SB_CONTA_EMAIL='{email}' SB_CONTA_CHAVE='{chave}' bash\" ]\n")


def _chave_dedicada(ctx: Ctx, person_id: int, nome: str) -> str:
    """Chave exclusiva da VM: nao conta como 'computador' do cliente (rotulo vm:, nao app:) e nao e revogada por outros logins."""
    chave = new_api_key()
    with ctx.store.tx() as c:
        c.execute("INSERT INTO keys(person_id,key_hash,key_last4,created_at,label) VALUES(?,?,?,?,?)", (person_id, sha256(chave), chave[-4:], ctx.clock(), "vm:" + nome))
    return chave


async def provisionar(ctx: Ctx, vm_id: int, contabo: Contabo | None = None) -> dict:
    vm = ctx.store.one("SELECT v.*, a.email, a.person_id FROM vms v JOIN accounts a ON a.id=v.account_id WHERE v.id=?", (vm_id,))
    if not vm:
        raise ContaboError("VM nao encontrada.")
    if vm["status"] not in ("requested", "error"):
        raise ContaboError(f"Esta VM ja esta {vm['status']}.")
    cfg = config(ctx)
    c = contabo or cliente(ctx)
    try:
        chave = _chave_dedicada(ctx, vm["person_id"], vm["name"])
        inst = await c.criar(nome=vm["name"], produto=cfg["product_id"], imagem=cfg["image_id"], regiao=cfg["region"], user_data=_user_data(vm["email"], chave),
                             ssh_keys=[int(cfg["ssh_secret_id"])] if cfg["ssh_secret_id"].isdigit() else None)
        _mudar(ctx, vm_id, status="provisioning", contabo_id=str(inst.get("instanceId") or ""), product_id=cfg["product_id"], region=cfg["region"],
               cost_cents=cfg["cost_cents"], price_cents=cfg["price_cents"], last_error=None)
    except ContaboError as e:
        _mudar(ctx, vm_id, status="error", last_error=str(e)[:300])
        raise
    finally:
        if contabo is None:
            await c.aclose()
    return ctx.store.one("SELECT * FROM vms WHERE id=?", (vm_id,))


async def _acao(ctx: Ctx, vm_id: int, acao: str, novo: str, contabo: Contabo | None) -> None:
    vm = ctx.store.one("SELECT * FROM vms WHERE id=?", (vm_id,))
    if not vm or not vm["contabo_id"]:
        raise ContaboError("Esta VM ainda nao existe na Contabo.")
    c = contabo or cliente(ctx)
    try:
        await c.acao(vm["contabo_id"], acao)
    finally:
        if contabo is None:
            await c.aclose()
    _mudar(ctx, vm_id, status=novo, suspended_at=ctx.clock() if novo == "suspended" else None)


async def suspender(ctx: Ctx, vm_id: int, contabo: Contabo | None = None) -> None:
    await _acao(ctx, vm_id, "shutdown", "suspended", contabo)


async def retomar(ctx: Ctx, vm_id: int, contabo: Contabo | None = None) -> None:
    await _acao(ctx, vm_id, "start", "running", contabo)


async def cancelar(ctx: Ctx, vm_id: int, contabo: Contabo | None = None) -> None:
    vm = ctx.store.one("SELECT * FROM vms WHERE id=?", (vm_id,))
    if not vm:
        raise ContaboError("VM nao encontrada.")
    if vm["contabo_id"]:
        c = contabo or cliente(ctx)
        try:
            await c.cancelar(vm["contabo_id"])
        finally:
            if contabo is None:
                await c.aclose()
    _mudar(ctx, vm_id, status="canceled")


def dias_de_atraso(ctx: Ctx, sub) -> float:
    """Dias desde o vencimento sem pagamento (0 se esta em dia)."""
    if not sub or sub["status"] == "active":
        return 0.0
    ref = sub["next_payment_at"] or sub["updated_at"]
    return max(0.0, (ctx.clock() - ref) / 86400)


def deve_ficar_desligada(ctx: Ctx, sub) -> bool:
    cfg = config(ctx)
    if not sub:
        return True
    if sub["status"] in ("canceled", "refunded", "chargeback"):
        return True
    return sub["status"] == "late" and dias_de_atraso(ctx, sub) > cfg["grace_days"]


def _cobranca(vm, sub):
    """A VM tem cobranca propria quando foi vendida como produto da Cakto (billing_status); senao vale o plano da conta."""
    if vm["billing_status"]:
        return {"status": vm["billing_status"], "next_payment_at": vm["next_payment_at"], "updated_at": vm["updated_at"]}
    return sub


# ---------------------------------------------------------------- webhook da Cakto (produto "maquina virtual")

def eh_produto_vm(ctx: Ctx, hint: str) -> bool:
    h = (hint or "").lower()
    extra = (ctx.store.get_setting("vm_product_hint") or "").strip().lower()
    return any(t in h for t in ("maquina virtual", "máquina virtual", "vps")) or bool(extra and extra in h)


def registrar_pagamento(ctx: Ctx, account_id: int, ev: dict) -> int | None:
    """Evento de pagamento do produto VM: cria o pedido na primeira compra paga e acompanha o estado da cobranca da VM."""
    now = ctx.clock()
    vm = None
    if ev.get("external_id"):
        vm = ctx.store.one("SELECT * FROM vms WHERE external_id=?", (ev["external_id"],))
    if vm is None:
        vm = ctx.store.one("SELECT * FROM vms WHERE account_id=? AND status!='canceled' AND external_id IS NULL ORDER BY id DESC LIMIT 1", (account_id,))
    if vm is None:
        if ev["status"] != "active":
            return None  # so uma compra paga abre pedido
        vm_id = solicitar(ctx, account_id)
        vm = ctx.store.one("SELECT * FROM vms WHERE id=?", (vm_id,))
    _mudar(ctx, vm["id"], external_id=ev.get("external_id") or vm["external_id"], billing_status=ev["status"],
           next_payment_at=ev.get("next_payment_at") or vm["next_payment_at"])
    return vm["id"]


async def varredura(ctx: Ctx, contabo: Contabo | None = None) -> dict:
    """Desliga a VM de quem passou da carencia e religa a de quem voltou a pagar. Idempotente; roda de tempos em tempos."""
    feito = {"suspensas": 0, "religadas": 0, "erros": 0}
    vms = ctx.store.query("SELECT * FROM vms WHERE status IN ('running','provisioning','suspended') AND contabo_id IS NOT NULL")
    if not vms:
        return feito
    c = contabo or cliente(ctx)
    try:
        for vm in vms:
            sub = _cobranca(vm, current_sub(ctx, vm["account_id"]))
            try:
                if vm["status"] != "suspended" and deve_ficar_desligada(ctx, sub):
                    await suspender(ctx, vm["id"], c)
                    feito["suspensas"] += 1
                elif vm["status"] == "suspended" and sub and sub["status"] in ("active", "late") and not deve_ficar_desligada(ctx, sub):
                    await retomar(ctx, vm["id"], c)
                    feito["religadas"] += 1
            except ContaboError as e:
                feito["erros"] += 1
                _mudar(ctx, vm["id"], last_error=str(e)[:300])
    finally:
        if contabo is None:
            await c.aclose()
    return feito


async def sincronizar(ctx: Ctx, contabo: Contabo | None = None) -> int:
    """Atualiza IP, recursos e estado das VMs a partir da Contabo (provisionando -> rodando)."""
    vms = ctx.store.query("SELECT * FROM vms WHERE contabo_id IS NOT NULL AND status IN ('provisioning','running','suspended')")
    if not vms:
        return 0
    c = contabo or cliente(ctx)
    n = 0
    try:
        todas = {str(i.get("instanceId")): i for i in await c.listar()}
        for vm in vms:
            i = todas.get(vm["contabo_id"])
            if not i:
                continue
            ip = ((i.get("ipConfig") or {}).get("v4") or {}).get("ip") or i.get("mainIp")
            campos = {"ip": ip, "cores": i.get("cpuCores"), "ram_mb": i.get("ramMb"), "disk_mb": i.get("diskMb")}
            if vm["status"] == "provisioning" and i.get("status") == "running":
                campos["status"] = "running"
            _mudar(ctx, vm["id"], **campos)
            n += 1
    finally:
        if contabo is None:
            await c.aclose()
    return n


async def provisionar_pagas(ctx: Ctx, contabo: Contabo | None = None) -> int:
    """Se o dono ligou vm_auto_provision: cria na Contabo as VMs cujo pagamento foi confirmado (billing_status active)."""
    cfg = config(ctx)
    if not (cfg["auto_provision"] and cfg["product_id"]):
        return 0
    n = 0
    for vm in ctx.store.query("SELECT id FROM vms WHERE status='requested' AND billing_status='active'"):
        try:
            await provisionar(ctx, vm["id"], contabo)
            n += 1
        except ContaboError:
            pass  # fica com status error e last_error; o painel mostra
    return n
