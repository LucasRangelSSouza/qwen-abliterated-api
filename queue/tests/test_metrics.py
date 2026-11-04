"""Metricas e faturamento do painel: log duravel de jobs, percentis, series por hora/dia, custo programado e dias de saldo."""
import time

import pytest

from app import metrics
from app.gpu.vast import Instance


def inst(iid, label, dph, status="running", **raw):
    return Instance(id=iid, actual_status=status, intended_status="running", status_msg=None, ssh_host="h", ssh_port=1, dph_total=dph,
                    storage_total_cost=0.02, machine_id=1, raw={"label": label, "gpu_name": "RTX 3090", "dph_base": dph - 0.02, **raw})


@pytest.mark.asyncio
async def test_log_e_series_por_hora(redis):
    now = 1_800_000_000.0
    for i, (wait, run, code) in enumerate([(1, 5, "200"), (2, 6, "200"), (30, 7, "200"), (0.5, 4, "500")]):
        end = now - 100 + i
        await metrics.log_job(redis, "llm", "chat", "llm-access", "Marina", end - wait - run, end - run, end, code)
    await metrics.log_job(redis, "image", "image", "llm-access", "dono", now - 50, now - 45, now - 40, "200")
    s = await metrics.summary(redis, "llm", hours=2, days=2, now=now)
    assert s["totals"]["requests"] == 4 and s["totals"]["errors"] == 1 and s["totals"]["ok"] == 3
    assert s["totals"]["wait_p95"] == 30 and s["totals"]["run_p50"] == 5
    assert s["peak_hour"]["requests"] == 4 and s["top_users"][0] == {"who": "Marina", "requests": 4}
    assert sum(h["requests"] for h in s["hourly"]) == 4 and sum(d["requests"] for d in s["daily"]) == 4
    img = await metrics.summary(redis, "image", hours=2, days=2, now=now)  # maquinas separadas
    assert img["totals"]["requests"] == 1 and img["top_users"][0]["who"] == "dono"


@pytest.mark.asyncio
async def test_so_metadados_no_log(redis):
    await metrics.log_job(redis, "llm", "chat", "c", "Ana", 1.0, 2.0, 3.0, "200")
    (_id, fields), = await redis.xrange(metrics.STREAM)
    assert {k.decode() for k in fields} == {"m", "k", "c", "u", "enq", "st", "end", "code", "cls"}  # nada de prompt nem resposta


@pytest.mark.asyncio
async def test_stream_tem_teto(redis, monkeypatch):
    monkeypatch.setattr(metrics, "STREAM_MAXLEN", 10)
    for i in range(300):
        await metrics.log_job(redis, "llm", "chat", "c", "", float(i), float(i), float(i + 1), "200")
    assert await redis.xlen(metrics.STREAM) < 300


def test_faturamento_dias_de_saldo_e_custo_programado():
    now = 1_800_000_000.0
    v = metrics.billing_view(77.0, [inst(1, "hold-cz", 0.289), inst(2, "midia-gpu-1", 0.1276), inst(3, "parada", 0.5, status="exited")], 1, 2, now=now)
    assert v["burn_per_hour_usd"] == pytest.approx(0.4166, abs=1e-3)  # a parada nao conta
    assert v["burn_per_day_usd"] == pytest.approx(10.0, abs=0.01) and v["days_of_balance"] == pytest.approx(7.7, abs=0.1)
    assert v["recharge_by"] == pytest.approx(now + 7.7 * 86400, abs=86400 * 0.2)
    assert v["recharge_needed_for_30d_usd"] == pytest.approx(v["burn_per_month_usd"] - 77.0, abs=0.01)
    assert v["machines"]["llm"]["monthly_usd"] == pytest.approx(0.289 * 720, abs=0.01) and v["machines"]["image"]["id"] == 2
    assert [m["id"] for m in v["machines"]["other"]] == [3]


def test_sem_consumo_nao_divide_por_zero():
    v = metrics.billing_view(10.0, [inst(3, "x", 0.5, status="exited")], None, None)
    assert v["days_of_balance"] is None and v["recharge_by"] is None


def test_percentil_borda():
    assert metrics._pct([], 95) is None and metrics._pct([3.0], 50) == 3.0 and metrics._pct([1, 2, 3, 4], 95) == 4
