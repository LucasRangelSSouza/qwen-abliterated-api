"""Painel das VMs de clientes (/admin/api/vms): lista, configuracao (credenciais da Contabo so de escrita) e acoes."""
from __future__ import annotations

from fastapi import Request

from app import vms as V
from app.admin import _db, _json, api, require
from app.ctx import fail
from app.ledger import iso


def _erro(e: V.ContaboError):
    return fail(502, str(e), "service_unavailable", "contabo_error")


def _view(r) -> dict:
    return {"id": r["id"], "account_id": r["account_id"], "email": r["email"], "name_client": r["acc_name"], "name": r["name"], "status": r["status"],
            "contabo_id": r["contabo_id"], "ip": r["ip"], "product_id": r["product_id"], "region": r["region"],
            "cores": r["cores"], "ram_mb": r["ram_mb"], "disk_mb": r["disk_mb"], "cost_cents": r["cost_cents"], "price_cents": r["price_cents"],
            "margin_cents": (r["price_cents"] or 0) - (r["cost_cents"] or 0), "created_at": iso(r["created_at"]),
            "suspended_at": iso(r["suspended_at"]) if r["suspended_at"] else None, "last_error": r["last_error"]}


@api.get("/vms")
@_db
def vms_list(request: Request):
    ctx = require(request)
    rows = ctx.store.query("SELECT v.*, a.email, a.name acc_name FROM vms v JOIN accounts a ON a.id=v.account_id ORDER BY v.id DESC")
    lista = [_view(r) for r in rows]
    ativos = [v for v in lista if v["status"] in ("running", "provisioning", "suspended")]
    return {"rows": lista, "total": {"vms": len(ativos), "cost_cents": sum(v["cost_cents"] for v in ativos), "price_cents": sum(v["price_cents"] for v in ativos)}}


@api.get("/vms/config")
@_db
def vms_config(request: Request):
    ctx = require(request)
    return {**V.config(ctx), "credentials_set": V.credenciais_configuradas(ctx), "secrets_key_set": bool(ctx.s.secrets_key)}


@api.put("/vms/config")
@_db
async def vms_config_put(request: Request):
    ctx = require(request)
    b = await _json(request)
    for chave, campo in (("product_id", "vm_product_id"), ("image_id", "vm_image_id"), ("region", "vm_region")):
        if chave in b:
            ctx.store.set_setting(campo, str(b[chave]).strip())
    for chave, campo in (("cost_cents", "vm_cost_cents"), ("price_cents", "vm_price_cents"), ("grace_days", "vm_grace_days")):
        if chave in b:
            try:
                v = int(b[chave])
            except (TypeError, ValueError):
                raise fail(422, f"{chave} precisa ser inteiro", "invalid_request_error", "invalid_value")
            if v < 0:
                raise fail(422, f"{chave} nao pode ser negativo", "invalid_request_error", "invalid_value")
            ctx.store.set_setting(campo, str(v))
    if "auto_provision" in b:
        ctx.store.set_setting("vm_auto_provision", "1" if b["auto_provision"] else "0")
    if isinstance(b.get("credentials"), dict):
        try:
            V.salvar_credenciais(ctx, b["credentials"])
        except V.ContaboError as e:
            raise fail(422, str(e), "invalid_request_error", "invalid_credentials")
    return {**V.config(ctx), "credentials_set": V.credenciais_configuradas(ctx), "secrets_key_set": bool(ctx.s.secrets_key)}


@api.post("/vms")
@_db
async def vms_create(request: Request):
    ctx = require(request)
    b = await _json(request)
    acc = ctx.store.one("SELECT id FROM accounts WHERE id=?", (b.get("account_id"),))
    if not acc:
        raise fail(404, "conta nao encontrada", "invalid_request_error", "not_found")
    return {"id": V.solicitar(ctx, acc["id"])}


async def _acao(request: Request, vm_id: int, fn):
    ctx = require(request)
    if not ctx.store.one("SELECT id FROM vms WHERE id=?", (vm_id,)):
        raise fail(404, "VM nao encontrada", "invalid_request_error", "not_found")
    try:
        await fn(ctx, vm_id)
    except V.ContaboError as e:
        raise _erro(e)
    return {"ok": True}


@api.post("/vms/{vm_id}/provision")
@_db
async def vms_provision(vm_id: int, request: Request):
    return await _acao(request, vm_id, V.provisionar)


@api.post("/vms/{vm_id}/suspend")
@_db
async def vms_suspend(vm_id: int, request: Request):
    return await _acao(request, vm_id, V.suspender)


@api.post("/vms/{vm_id}/resume")
@_db
async def vms_resume(vm_id: int, request: Request):
    return await _acao(request, vm_id, V.retomar)


@api.post("/vms/{vm_id}/cancel")
@_db
async def vms_cancel(vm_id: int, request: Request):
    return await _acao(request, vm_id, V.cancelar)


@api.post("/vms/sync")
@_db
async def vms_sync(request: Request):
    ctx = require(request)
    try:
        return {"atualizadas": await V.sincronizar(ctx), **await V.varredura(ctx)}
    except V.ContaboError as e:
        raise _erro(e)
