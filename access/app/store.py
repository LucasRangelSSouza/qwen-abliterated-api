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
  session_seconds INTEGER NOT NULL DEFAULT 18000, week_seconds INTEGER NOT NULL DEFAULT 604800, custom INTEGER NOT NULL DEFAULT 0);
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
CREATE TABLE IF NOT EXISTS safety_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, classifier TEXT NOT NULL CHECK(classifier IN ('input','output')),
  person_id INTEGER, category TEXT, score REAL, route TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS safety_log_ts ON safety_log(ts);
CREATE TABLE IF NOT EXISTS plans(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, session_limit_tokens INTEGER, week_limit_tokens INTEGER, image_quota_month INTEGER NOT NULL DEFAULT 0,
  video_quota_month INTEGER, audio_quota_month INTEGER,
  safety_level INTEGER NOT NULL DEFAULT 1 CHECK(safety_level IN (1,2,3)), max_devices INTEGER NOT NULL DEFAULT 3, price_cents INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS media_overrides(
  person_id INTEGER PRIMARY KEY, image_quota_month INTEGER, video_quota_month INTEGER, audio_quota_month INTEGER);
CREATE TABLE IF NOT EXISTS credits(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tokens','image','video','audio')),
  units INTEGER NOT NULL, remaining INTEGER NOT NULL, granted_at REAL NOT NULL, expires_at REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'purchase', external_id TEXT UNIQUE, revoked_at REAL);
CREATE INDEX IF NOT EXISTS credits_person ON credits(person_id, kind, expires_at);
CREATE TABLE IF NOT EXISTS media_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, person_id INTEGER NOT NULL, kind TEXT NOT NULL, units INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS media_usage_person ON media_usage(person_id, kind, ts);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '', pw_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','blocked')), person_id INTEGER NOT NULL REFERENCES people(id),
  setup_hash TEXT, setup_expires REAL, created_at REAL NOT NULL, last_login REAL);
CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id), plan TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','active','late','canceled','refunded','chargeback')), provider TEXT NOT NULL DEFAULT 'cakto',
  external_id TEXT, next_payment_at REAL, amount_cents INTEGER, started_at REAL, updated_at REAL NOT NULL, blocked_by_billing INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS subscriptions_account ON subscriptions(account_id);
CREATE TABLE IF NOT EXISTS vms(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id), name TEXT NOT NULL UNIQUE, contabo_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('requested','provisioning','running','suspended','canceled','error')), product_id TEXT, region TEXT, ip TEXT,
  cost_cents INTEGER NOT NULL DEFAULT 0, price_cents INTEGER NOT NULL DEFAULT 0, cores INTEGER, ram_mb INTEGER, disk_mb INTEGER,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, suspended_at REAL, last_error TEXT,
  external_id TEXT, billing_status TEXT, next_payment_at REAL);
CREATE INDEX IF NOT EXISTS vms_account ON vms(account_id);
CREATE TABLE IF NOT EXISTS free_trials(
  account_id INTEGER PRIMARY KEY REFERENCES accounts(id), ident TEXT NOT NULL UNIQUE, used INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0,
  code_hash TEXT, code_expires REAL, code_tries INTEGER NOT NULL DEFAULT 0, ip TEXT, created_at REAL NOT NULL, last_at REAL);
CREATE TABLE IF NOT EXISTS billing_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL, received_at REAL NOT NULL,
  processed INTEGER NOT NULL DEFAULT 0, error TEXT, payload TEXT NOT NULL, UNIQUE(provider, event_id));
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
            # migracao: nivel de seguranca por pessoa. DEFAULT 1 = sem trava: quem ja existe nao muda de comportamento
            if "label" not in {r["name"] for r in c.execute("PRAGMA table_info(keys)").fetchall()}:
                c.execute("ALTER TABLE keys ADD COLUMN label TEXT")  # "app:<computador>" nas chaves emitidas no login
            if "safety_level" not in {r["name"] for r in c.execute("PRAGMA table_info(people)").fetchall()}:
                c.execute("ALTER TABLE people ADD COLUMN safety_level INTEGER NOT NULL DEFAULT 1 CHECK(safety_level IN (1,2,3))")
            if "custom" not in {r["name"] for r in c.execute("PRAGMA table_info(limits)").fetchall()}:
                c.execute("ALTER TABLE limits ADD COLUMN custom INTEGER NOT NULL DEFAULT 0")  # 1 = limite editado a mao na pessoa (vale no lugar do plano)
            cols_plans = {r["name"] for r in c.execute("PRAGMA table_info(plans)").fetchall()}
            for col in ("video_quota_month", "audio_quota_month"):  # cota de midia por plano (NULL = ainda nao configurada, nao bloqueia)
                if col not in cols_plans:
                    c.execute(f"ALTER TABLE plans ADD COLUMN {col} INTEGER")
            cols_vms = {r["name"] for r in c.execute("PRAGMA table_info(vms)").fetchall()}
            for col, tipo in (("external_id", "TEXT"), ("billing_status", "TEXT"), ("next_payment_at", "REAL")):  # cobranca propria da VM (produto da Cakto)
                if col not in cols_vms:
                    c.execute(f"ALTER TABLE vms ADD COLUMN {col} {tipo}")
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
