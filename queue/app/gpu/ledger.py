"""Livro-razao de gasto da GPU (Spec 13 §8.1): horas ligadas x preco travado, disco parado e extras (download, tentativas perdidas).
Estado em Redis para sobreviver a reinicio do servico. Mes e dia em UTC."""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Callable

K_OPEN = "llmq:ledger:open"
K_CLOSED = "llmq:ledger:closed"


def month_start(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc).timestamp()


def day_start(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()


def _cost(rec: dict, t0: float, t1: float, now: float) -> float:
    if rec["kind"] == "extra":
        return rec["cost"] if t0 <= rec["start"] < t1 else 0.0
    a = max(rec["start"], t0)
    b = min(rec["end"] if rec.get("end") is not None else now, t1)
    return max(0.0, b - a) / 3600.0 * rec["rate"]


class Ledger:
    def __init__(self, redis, clock: Callable[[], float] = time.time):
        self.r, self.clock = redis, clock

    async def begin(self, kind: str, instance_id: int, rate_h: float, now: float | None = None) -> None:
        """kind: 'run' (GPU ligada, preco total/h) ou 'stopped' (so disco, US$/h de armazenamento)."""
        now = self.clock() if now is None else now
        await self.r.hset(K_OPEN, f"{kind}:{instance_id}", json.dumps({"kind": kind, "id": instance_id, "start": now, "rate": rate_h}))

    async def end(self, kind: str, instance_id: int, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        raw = await self.r.hget(K_OPEN, f"{kind}:{instance_id}")
        if not raw:
            return
        rec = json.loads(raw)
        rec["end"] = now
        await self.r.hdel(K_OPEN, f"{kind}:{instance_id}")
        await self.r.rpush(K_CLOSED, json.dumps(rec))

    async def add_cost(self, usd: float, note: str, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        await self.r.rpush(K_CLOSED, json.dumps({"kind": "extra", "start": now, "end": now, "cost": usd, "note": note}))

    async def _records(self) -> list[dict]:
        closed = [json.loads(x) for x in await self.r.lrange(K_CLOSED, 0, -1)]
        opened = [json.loads(v) for v in (await self.r.hvals(K_OPEN))]
        return closed + opened

    async def spend(self, t0: float, t1: float, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        return round(sum(_cost(r, t0, t1, now) for r in await self._records()), 6)

    async def month_spend(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        return await self.spend(month_start(now), now + 1, now)

    async def day_spend(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        return await self.spend(day_start(now), now + 1, now)

    async def open_run_hours(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        return sum((now - r["start"]) / 3600.0 for r in [json.loads(v) for v in await self.r.hvals(K_OPEN)] if r["kind"] == "run")

    async def snapshot(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        return {"month_usd": await self.month_spend(now), "day_usd": await self.day_spend(now)}
