"""Aba de custos: fixos editáveis (BRL e USD), Vast e Runware medidos, total do mês e valor para carregar no cartão (+15%)."""
import httpx

from tests.conftest import Rig


async def test_custos_total_e_carga(rig: Rig, monkeypatch):
    from app import custos, fornecedores
    custos._cotacao.update(v=5.0, t=10**12)

    async def falso_get(url, headers=None, timeout=None):
        if url.endswith("/admin/billing"):
            return httpx.Response(200, json={"burn_per_month_usd": 100.0, "credit_usd": 40.0, "days_of_balance": 12})
        raise httpx.ConnectError("x")
    monkeypatch.setattr(rig.app.state.ctx.http, "get", falso_get)

    async def rw(ctx, k=None):
        return {"ok": True, "saldo_usd": 3.0, "gasto_30d_usd": 2.0}
    monkeypatch.setattr(fornecedores, "saldo_runware", rw)
    await rig.login()
    r = await rig.client.post("/admin/api/custos/fixos", json={"fixos": [{"nome": "Contabo", "valor": "200,00", "moeda": "BRL"},
                                                                          {"nome": "ChatGPT", "valor": 20, "moeda": "USD"}, {"nome": ""}]})
    assert r.status_code == 200 and len(r.json()["fixos"]) == 2
    j = (await rig.client.get("/admin/api/custos")).json()
    # 200 + 20*5 + Vast 100*5 + Runware 2*5 = 810
    assert j["total_brl_mes"] == 810.0 and j["carregar_brl"] == round(810 * 1.15, 2)
    assert any(i["nome"].startswith("GPU da IA") and i["ok"] for i in j["itens"])


async def test_custos_valor_invalido(rig: Rig):
    await rig.login()
    r = await rig.client.post("/admin/api/custos/fixos", json={"fixos": [{"nome": "X", "valor": "abc"}]})
    assert r.status_code == 422
