"""Metricas e custo para o painel de gestao.

1) Log duravel de jobs (Redis stream com teto): cada pedido encerrado de qualquer maquina grava uma linha so com metadados
   (quem, tipo, tempos, codigo). Nunca o conteudo. Dele saem os graficos de pico de uso e de latencia.
2) Faturamento: saldo da Vast, preco real por hora de cada maquina (GPU + disco + trafego), custo programado por mes, quantos
   dias o saldo cobre e a data em que a recarga fica necessaria."""
from __future__ import annotations

import math
import time

STREAM = "llmq:joblog"
STREAM_MAXLEN = 30000   # ~30 mil pedidos (semanas de uso normal); acima disso os mais antigos saem


async def log_job(redis, machine: str, kind: str, consumer: str, end_user: str, enq: float, start: float | None, end: float, code: str,
                  cls: str = "interactive") -> None:
    """Grava um job encerrado. `machine` e 'llm' (chat/embed) ou 'image'. Falha de registro nunca derruba o pedido."""
    try:
        await redis.xadd(STREAM, {"m": machine, "k": kind, "c": consumer, "u": end_user or "", "enq": f"{enq:.3f}", "st": f"{start:.3f}" if start else "",
                                  "end": f"{end:.3f}", "code": str(code), "cls": cls}, maxlen=STREAM_MAXLEN, approximate=True)
    except Exception:  # noqa: BLE001
        pass


def _f(v) -> float | None:
    try:
        s = v.decode() if isinstance(v, bytes) else v
        return float(s) if s not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v or "")


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    i = min(len(vals) - 1, max(0, math.ceil(p / 100 * len(vals)) - 1))
    return round(vals[i], 2)


async def rows(redis, machine: str, since: float) -> list[dict]:
    """Linhas da maquina desde `since` (epoch), mais antigas primeiro."""
    out = []
    for _id, f in await redis.xrange(STREAM, min="-", max="+"):  # teto de 30 mil linhas: ler tudo e barato
        g = {_s(k): v for k, v in f.items()}
        if _s(g.get("m")) != machine:
            continue
        enq, st, end = _f(g.get("enq")), _f(g.get("st")), _f(g.get("end"))
        if end is None or enq is None or end < since:
            continue
        out.append({"kind": _s(g.get("k")), "consumer": _s(g.get("c")), "user": _s(g.get("u")), "enq": enq, "start": st, "end": end,
                    "code": _s(g.get("code")), "wait": round((st or end) - enq, 3), "run": round(end - st, 3) if st else None})
    return out


def _bucket(rs: list[dict], width: int, since: float, until: float) -> list[dict]:
    n = max(1, int((until - since) // width) + 1)
    b = [{"t": since + i * width, "n": 0, "err": 0, "w": [], "r": [], "busy": 0.0} for i in range(n)]
    for r in rs:
        i = int((r["end"] - since) // width)
        if 0 <= i < n:
            x = b[i]
            x["n"] += 1
            x["err"] += 0 if r["code"] in ("200", "done") else 1
            x["w"].append(r["wait"])
            if r["run"] is not None:
                x["r"].append(r["run"])
                x["busy"] += r["run"]
    return [{"t": x["t"], "requests": x["n"], "errors": x["err"], "wait_p50": _pct(x["w"], 50), "wait_p95": _pct(x["w"], 95),
             "run_p50": _pct(x["r"], 50), "run_p95": _pct(x["r"], 95), "busy_pct": round(min(100.0, 100 * x["busy"] / width), 1)} for x in b]


async def summary(redis, machine: str, hours: int = 24, days: int = 14, now: float | None = None) -> dict:
    """Series por hora (ultimas `hours`) e por dia (ultimos `days`), picos, totais e latencia, para os graficos do painel."""
    now = now or time.time()
    since_d = now - days * 86400
    rs = await rows(redis, machine, since_d)
    hour_since = (now - hours * 3600) // 3600 * 3600
    day_since = (now - days * 86400) // 86400 * 86400
    hourly = _bucket([r for r in rs if r["end"] >= hour_since], 3600, hour_since, now)
    daily = _bucket(rs, 86400, day_since, now)
    peak = max(hourly, key=lambda x: x["requests"], default=None)
    ok = [r for r in rs if r["code"] in ("200", "done")]
    return {"machine": machine, "hourly": hourly, "daily": daily,
            "peak_hour": {"t": peak["t"], "requests": peak["requests"]} if peak and peak["requests"] else None,
            "totals": {"requests": len(rs), "ok": len(ok), "errors": len(rs) - len(ok),
                       "wait_p50": _pct([r["wait"] for r in rs], 50), "wait_p95": _pct([r["wait"] for r in rs], 95),
                       "run_p50": _pct([r["run"] for r in rs if r["run"] is not None], 50), "run_p95": _pct([r["run"] for r in rs if r["run"] is not None], 95)},
            "top_users": _top(rs)}


def _top(rs: list[dict], n: int = 8) -> list[dict]:
    c: dict[str, int] = {}
    for r in rs:
        k = r["user"] or r["consumer"]
        c[k] = c.get(k, 0) + 1
    return [{"who": k, "requests": v} for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:n]]


# ---------------------------------------------------------------- faturamento

def machine_cost(inst) -> dict:
    """Preco real por hora de uma instancia da Vast: GPU + disco + trafego tipico."""
    raw = getattr(inst, "raw", {}) or {}
    dph = float(getattr(inst, "dph_total", 0) or 0)
    return {"id": inst.id, "label": raw.get("label"), "status": inst.actual_status, "gpu": raw.get("gpu_name"), "num_gpus": raw.get("num_gpus"),
            "location": raw.get("geolocation"), "dph": round(dph, 4), "dph_gpu": round(float(raw.get("dph_base") or 0), 4),
            "storage_dph": round(float(raw.get("storage_total_cost") or 0), 4), "disk_gb": raw.get("disk_space"),
            "cpu_ram_gb": round((raw.get("cpu_ram") or 0) / 1024), "started": raw.get("start_date"), "offer_end": raw.get("end_date"),
            "running": inst.actual_status == "running", "monthly_usd": round(dph * 24 * 30, 2)}


def billing_view(credit: float, insts: list, llm_id: int | None, media_id: int | None, spent_month: float | None = None, now: float | None = None) -> dict:
    """Saldo, consumo por dia, dias de cobertura e quando recarregar. Soma so as maquinas ligadas (parada nao gasta GPU)."""
    now = now or time.time()
    machines = {"llm": None, "image": None, "other": []}
    burn_h = 0.0
    for i in insts:
        m = machine_cost(i)
        if llm_id and i.id == llm_id:
            machines["llm"] = m
        elif media_id and i.id == media_id:
            machines["image"] = m
        else:
            machines["other"].append(m)
        if m["running"]:
            burn_h += m["dph"]
    per_day = burn_h * 24
    days = credit / per_day if per_day > 0 else None
    return {"credit_usd": round(credit, 2), "burn_per_hour_usd": round(burn_h, 4), "burn_per_day_usd": round(per_day, 2),
            "burn_per_month_usd": round(per_day * 30, 2), "days_of_balance": round(days, 1) if days is not None else None,
            "recharge_by": now + days * 86400 if days is not None else None,
            "recharge_needed_for_30d_usd": round(max(0.0, per_day * 30 - credit), 2), "machines": machines}
