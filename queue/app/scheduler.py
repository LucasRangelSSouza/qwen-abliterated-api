"""Pistas de concorrencia (chat e embedding): FIFO por classe, `interactive` antes de `batch`, limite por consumidor (Spec 13 §3.1).
As conexoes dos clientes vivem neste processo, entao a fila e em memoria; o estado duravel (gasto, hosts, instancia) fica em Redis."""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field

RANK = {"interactive": 0, "batch": 1}


class QueueFull(Exception):
    def __init__(self, retry_after: int = 30):
        super().__init__("fila cheia")
        self.retry_after = retry_after


@dataclass
class Ticket:
    consumer: str
    cls: str
    seq: int
    rank: int
    t_enq: float
    admitted: bool = False
    t_start: float | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class Lane:
    def __init__(self, name: str, limit: int, queue_max: int, per_consumer: int | None = None):
        self.name, self.limit, self.queue_max = name, limit, queue_max
        self.per_consumer = per_consumer or limit
        self.waiting: list[Ticket] = []
        self.inflight = 0
        self.by_consumer: dict[str, int] = {}
        self._cond = asyncio.Condition()
        self._seq = itertools.count()
        self.served = 0

    def admit(self, consumer: str, cls: str, now: float) -> Ticket:
        if len(self.waiting) >= self.queue_max:
            raise QueueFull(retry_after=30)
        t = Ticket(consumer, cls, next(self._seq), RANK.get(cls, 0), now)
        self.waiting.append(t)
        self.waiting.sort(key=lambda x: (x.rank, x.seq))
        return t

    def _eligible(self) -> Ticket | None:
        if self.inflight >= self.limit:
            return None
        for t in self.waiting:
            if self.by_consumer.get(t.consumer, 0) < self.per_consumer:
                return t
        return None

    async def acquire(self, t: Ticket, now: float) -> None:
        async with self._cond:
            while True:
                if self._eligible() is t:
                    self.waiting.remove(t)
                    self.inflight += 1
                    self.by_consumer[t.consumer] = self.by_consumer.get(t.consumer, 0) + 1
                    t.admitted, t.t_start = True, now
                    return
                await self._cond.wait()

    async def release(self, t: Ticket) -> None:
        async with self._cond:
            if t.admitted:
                self.inflight -= 1
                self.by_consumer[t.consumer] = max(0, self.by_consumer.get(t.consumer, 1) - 1)
                t.admitted = False
                self.served += 1
            self._cond.notify_all()

    async def cancel(self, t: Ticket) -> None:
        """Cliente desistiu enquanto esperava (ou terminou): sai da fila e libera o lugar."""
        async with self._cond:
            if t in self.waiting:
                self.waiting.remove(t)
            self._cond.notify_all()
        await self.release(t)

    def position(self, t: Ticket) -> int:
        try:
            return self.waiting.index(t) + 1
        except ValueError:
            return 0

    def stats(self) -> dict:
        return {"waiting": len(self.waiting), "inflight": self.inflight, "limit": self.limit, "served": self.served}
