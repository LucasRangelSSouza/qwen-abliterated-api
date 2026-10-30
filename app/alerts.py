"""Alertas no webhook do Google Chat, com deduplicacao. Sem webhook configurado, so registra em log."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("llm-queue.alerts")


class Alerts:
    def __init__(self, webhook_url: str = "", dedupe_s: float = 600.0, client: httpx.AsyncClient | None = None):
        self.url, self.dedupe_s = webhook_url, dedupe_s
        self._last: dict[str, float] = {}
        self.history: list[dict] = []
        self._c = client or httpx.AsyncClient(timeout=10.0)

    async def notify(self, key: str, text: str, level: str = "warning") -> bool:
        now = time.time()
        if now - self._last.get(key, 0) < self.dedupe_s:
            return False
        self._last[key] = now
        self.history = (self.history + [{"t": now, "key": key, "text": text, "level": level}])[-50:]
        log.warning("ALERTA %s: %s", key, text)
        if not self.url:
            return True
        try:
            await self._c.post(self.url, json={"text": f"[llm-queue] {text}"})
        except Exception as e:  # noqa: BLE001
            log.error("webhook falhou: %r", e)
        return True
