"""Camada SQLite (stdlib, sem ORM). Uma conexao com lock; reservas usam BEGIN IMMEDIATE."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')), created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS keys(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id),
  key_hash TEXT NOT NULL UNIQUE, key_last4 TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS limits(
  person_id INTEGER PRIMARY KEY REFERENCES people(id), session_limit_tokens INTEGER, week_limit_tokens INTEGER,
  session_seconds INTEGER NOT NULL DEFAULT 18000, week_seconds INTEGER NOT NULL DEFAULT 604800);
CREATE TABLE IF NOT EXISTS windows(
  person_id INTEGER NOT NULL REFERENCES people(id), kind TEXT NOT NULL CHECK(kind IN ('session','week')),
  start_ts REAL NOT NULL, end_ts REAL NOT NULL, used_tokens INTEGER NOT NULL DEFAULT 0,
  reserved_tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(person_id, kind));
CREATE TABLE IF NOT EXISTS usage_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, person_id INTEGER, key_id INTEGER, actor TEXT,
  route TEXT NOT NULL, in_tokens INTEGER NOT NULL DEFAULT 0, out_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, limits_json TEXT);
CREATE INDEX IF NOT EXISTS usage_log_ts ON usage_log(ts);
CREATE INDEX IF NOT EXISTS usage_log_person ON usage_log(person_id, ts);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    """Conecta sob demanda: se o arquivo estiver indisponivel so as chamadas ao banco falham (chaves OWNER/PLATFORM seguem)."""

    def __init__(self, path: str):
        self.path = path
        self._c: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _conn(self) -> sqlite3.Connection:
        if self._c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript(SCHEMA)
            self._c = c
        return self._c

    @contextmanager
    def tx(self):
        with self._lock:
            c = self._conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def get_setting(self, key: str) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else None

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def purge_usage(self, before_ts: float) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM usage_log WHERE ts < ?", (before_ts,))

    def close(self) -> None:
        with self._lock:
            if self._c is not None:
                self._c.close()
                self._c = None
