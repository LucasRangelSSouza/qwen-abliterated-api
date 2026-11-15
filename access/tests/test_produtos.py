"""Painel do ecossistema: agrega os produtos externos pela chave de admin de cada um; produto fora do ar não derruba."""
import json

import httpx

from tests.conftest import Rig


async def test_produtos_agrega_e_tolera_falha(rig: Rig, monkeypatch):
    monkeypatch.setenv("PRODUTOS_ADMIN", json.dumps([{"id": "cortai", "nome": "Cortaí", "url": "https://cortai.x", "chave": "k1"},
                                                     {"id": "rendai", "nome": "Rendaí", "url": "https://rendai.x", "chave": "k2"}]))
    vistos = []

    async def falso_get(url, headers=None, timeout=None):
        vistos.append((url, headers.get("x-admin-key")))
        if "rendai" in url:
            raise httpx.ConnectError("fora")
        return httpx.Response(200, json={"assinantes": 3, "receita_mensal": 89.7})
    ctx = rig.app.state.ctx
    monkeypatch.setattr(ctx.http, "get", falso_get)
    await rig.login()
    r = await rig.client.get("/admin/api/produtos")
    assert r.status_code == 200
    ps = {p["id"]: p for p in r.json()["produtos"]}
    assert ps["cortai"]["assinantes"] == 3 and "erro" in ps["rendai"]
    assert ("https://cortai.x/api/admin/resumo", "k1") in vistos
    r = await rig.client.get("/admin/api/produtos/naoexiste/assinantes")
    assert r.status_code == 404


async def test_produtos_exige_login(rig: Rig):
    r = await rig.client.get("/admin/api/produtos")
    assert r.status_code == 401
