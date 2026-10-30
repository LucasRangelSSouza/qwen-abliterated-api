"""Log do ciclo de vida da GPU (Spec 14 §5.3.1, tela Maquinas): uma linha por ligada, do pedido ate o desligamento."""
from __future__ import annotations

import datetime as _dt
import json
from typing import Callable

K_CUR = "llmq:cycle_cur"
K_LIST = "llmq:cycles"
KEEP = 300


def iso(ts: float | None) -> str | None:
    return None if ts is None else _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CycleLog:
    def __init__(self, redis, clock: Callable[[], float]):
        self.r, self.clock = redis, clock
        self.cur: dict | None = None

    async def open(self, path: str) -> None:
        t0 = self.clock()
        kind = {"restart": "restart", "adopt": "adopt"}.get(path, "cold")
        self.cur = {"id": int(t0 * 1000), "kind": kind, "path": path, "t0": t0, "ready_at": None, "running_at": None,
                    "host": None, "geo": None, "price_dph": None, "stages": {}, "failed": False, "fail_reason": None}
        await self._save()

    async def mark(self, **kv) -> None:
        if self.cur is None:
            return
        self.cur.update(kv)
        await self._save()

    async def stage(self, **kv) -> None:
        if self.cur is not None:
            self.cur["stages"].update(kv)
            await self._save()

    async def close(self, reason: str, cost_usd: float, failed: bool = False) -> None:
        c = self.cur
        if c is None:
            return
        self.cur = None
        c.update({"stopped_at": self.clock(), "stop_reason": reason, "cost_usd": round(cost_usd, 4), "failed": failed or c.get("failed", False)})
        await self.r.lpush(K_LIST, json.dumps(self._public(c)))
        await self.r.ltrim(K_LIST, 0, KEEP - 1)
        await self.r.delete(K_CUR)

    async def _save(self) -> None:
        await self.r.set(K_CUR, json.dumps(self.cur))

    async def restore(self) -> None:
        raw = await self.r.get(K_CUR)
        if raw and self.cur is None:
            self.cur = json.loads(raw)

    @staticmethod
    def _public(c: dict) -> dict:
        t0, ready = c.get("t0"), c.get("ready_at")
        cold = None if ready is None else round(ready - t0, 1)
        st = dict(c.get("stages") or {})
        if cold is not None and st:
            rest = cold - sum(v for v in st.values() if isinstance(v, (int, float)))
            st.setdefault("warmup_s", round(max(0.0, rest), 1))
        return {"id": c["id"], "kind": c["kind"], "path": c.get("path"), "started_at": iso(t0), "ready_at": iso(ready),
                "cold_start_s": cold, "stages": st, "host": c.get("host"), "geo": c.get("geo"), "price_dph": c.get("price_dph"),
                "stopped_at": iso(c.get("stopped_at")), "stop_reason": c.get("stop_reason"), "cost_usd": c.get("cost_usd"),
                "failed": bool(c.get("failed")), "fail_reason": c.get("fail_reason")}

    async def list(self, limit: int = 50) -> list[dict]:
        rows = [json.loads(x) for x in await self.r.lrange(K_LIST, 0, max(0, limit - 1))]
        if self.cur is not None:
            rows.insert(0, self._public(self.cur))
        return rows[:limit]
