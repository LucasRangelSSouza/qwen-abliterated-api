"""Janelas de uso: reserva atomica, reconciliacao e visao (regras de DESIGN.md, Spec 14 §5.3).
Uma janela so esta "aberta" se nao venceu e tem consumo ou reserva; reserva desfeita sem consumo deixa a janela fechada,
entao pedido que falha antes de qualquer token real nao abre janela."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

KINDS = ("session", "week")
PURGE_EVERY_S = 3600


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Blocked(Exception):
    def __init__(self, window: str, resets_at: float | None, remaining: int, needed: int, limit: int):
        super().__init__(window)
        self.window, self.resets_at, self.remaining, self.needed, self.limit = window, resets_at, remaining, needed, limit


@dataclass
class Reservation:
    person_id: int
    amount: int
    held: list = field(default_factory=list)  # (kind, start_ts da janela que recebeu a reserva)


def _active(w, now: float) -> bool:
    return bool(w) and w["end_ts"] > now and (w["used_tokens"] + w["reserved_tokens"]) > 0


def view(w, limit: int | None, now: float) -> dict:
    act = _active(w, now)
    used = w["used_tokens"] if act else 0
    held = w["reserved_tokens"] if act else 0
    return {"limit": limit, "used": used, "remaining": None if limit is None else max(0, limit - used - held),
            "resets_at": iso(w["end_ts"]) if act else None}


class Ledger:
    def __init__(self, store, clock, retention_days: int = 90):
        self.store, self.clock, self.retention_days = store, clock, retention_days
        self._last_purge = 0.0

    def limits(self, person_id: int) -> dict:
        r = self.store.one("SELECT * FROM limits WHERE person_id=?", (person_id,))
        return dict(r) if r else {"person_id": person_id, "session_limit_tokens": None, "week_limit_tokens": None,
                                  "session_seconds": 18000, "week_seconds": 604800}

    def reserve(self, person_id: int, amount: int) -> Reservation:
        now = self.clock()
        with self.store.tx() as c:
            lim = c.execute("SELECT * FROM limits WHERE person_id=?", (person_id,)).fetchone()
            plan = []
            for kind in KINDS:
                limit, secs = lim[f"{kind}_limit_tokens"], lim[f"{kind}_seconds"]
                w = c.execute("SELECT * FROM windows WHERE person_id=? AND kind=?", (person_id, kind)).fetchone()
                act = _active(w, now)
                if limit is not None:
                    taken = w["used_tokens"] + w["reserved_tokens"] if act else 0
                    remaining = max(0, limit - taken)
                    if amount > remaining:
                        raise Blocked(kind, w["end_ts"] if act else None, remaining, amount, limit)
                plan.append((kind, w, act, secs))
            res = Reservation(person_id, amount)
            for kind, w, act, secs in plan:
                if act:
                    c.execute("UPDATE windows SET reserved_tokens=reserved_tokens+? WHERE person_id=? AND kind=?", (amount, person_id, kind))
                    res.held.append((kind, w["start_ts"]))
                else:
                    c.execute("INSERT OR REPLACE INTO windows(person_id,kind,start_ts,end_ts,used_tokens,reserved_tokens) VALUES(?,?,?,?,0,?)",
                              (person_id, kind, now, now + secs, amount))
                    res.held.append((kind, now))
            return res

    def _settle(self, res: Reservation, charge: int) -> None:
        with self.store.tx() as c:
            for kind, start in res.held:  # janela trocada (reset do admin ou vencida e reaberta): ignora
                c.execute("UPDATE windows SET reserved_tokens=MAX(0, reserved_tokens-?), used_tokens=used_tokens+? "
                          "WHERE person_id=? AND kind=? AND start_ts=?", (res.amount, charge, res.person_id, kind, start))

    def commit(self, res: Reservation, total: int) -> None:
        self._settle(res, max(1, int(total)))

    def release(self, res: Reservation) -> None:
        self._settle(res, 0)

    def windows(self, person_id: int) -> dict:
        now = self.clock()
        lim = self.limits(person_id)
        rows = {r["kind"]: r for r in self.store.query("SELECT * FROM windows WHERE person_id=?", (person_id,))}
        return {k: view(rows.get(k), lim[f"{k}_limit_tokens"], now) for k in KINDS}

    def log(self, *, person_id, key_id, actor, route, in_tokens, out_tokens, total, status, limits: dict | None) -> None:
        """Registra so numeros e rotulos; nunca texto de mensagem."""
        now = self.clock()
        lj = json.dumps({"session_limit_tokens": limits["session_limit_tokens"], "week_limit_tokens": limits["week_limit_tokens"]}) if limits else None
        with self.store.tx() as c:
            c.execute("INSERT INTO usage_log(ts,person_id,key_id,actor,route,in_tokens,out_tokens,total_tokens,status,limits_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (now, person_id, key_id, actor, route, int(in_tokens), int(out_tokens), int(total), status, lj))
        if now - self._last_purge > PURGE_EVERY_S:
            self.purge(now)

    def purge(self, now: float | None = None) -> None:
        now = now or self.clock()
        self._last_purge = now
        self.store.purge_usage(now - self.retention_days * 86400)
