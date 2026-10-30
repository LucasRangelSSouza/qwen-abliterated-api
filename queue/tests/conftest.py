import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.alerts import Alerts
from app.config import Settings
from app.gpu.controller import GPUController
from app.gpu.ledger import Ledger
from app.gpu.tunnel import NoTunnel
from tests.fakes import FakeVast


def fast_settings(**kw) -> Settings:
    base = dict(tunnel_mode="none", poll_s=0.01, reconcile_s=0.05, scheduling_timeout_s=0.3, running_timeout_s=0.6, ssh_timeout_s=0.3,
                health_timeout_s=1.5, wait_max_s=4.0, search_retry_s=0.05, search_window_s=0.3, idle_min=0.002, stopped_ttl_h=1.0,
                keepalive_s=0.05, hedge_max_s=0.5, hedge_enabled=False, max_waste_per_wake_usd=5.0, min_credit_usd=1.0,
                monthly_budget_usd=1000.0, max_daily_usd=1000.0)
    base.update(kw)
    return Settings(**base)


class Probe:
    def __init__(self):
        self.ok = True
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.ok


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def make_ctl(redis):
    made = []

    async def _make(settings: Settings | None = None, vast: FakeVast | None = None, probe: Probe | None = None) -> tuple[GPUController, FakeVast, Probe]:
        s = settings or fast_settings()
        v = vast or FakeVast()
        p = probe or Probe()
        ctl = GPUController(s, v, NoTunnel(), Ledger(redis), Alerts(""), redis, health_probe=p, onstart="#!/bin/bash\necho onstart\n")
        made.append(ctl)
        return ctl, v, p

    yield _make
    for c in made:
        await c.stop_loop()
