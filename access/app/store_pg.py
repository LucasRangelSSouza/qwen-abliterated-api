"""Adaptador Postgres do Store (Postgres unificado da VPS, schema proprio). Mesma interface do `Store` SQLite: `query`, `one`, `tx`,
`get_setting`, `set_setting`, `purge_usage`, `close`. O SQL do resto do app continua o mesmo (placeholders `?`); aqui ele e traduzido:
`?` vira `%s`, `LIKE` vira `ILIKE` (o SQLite ignora maiuscula, o Postgres nao), `COLLATE NOCASE` vira `LOWER()`, `MAX(0,x)` vira `GREATEST`,
`lastrowid` vem de `RETURNING id` e `rowcount` e repassado. A serializacao que o SQLite faz com `BEGIN IMMEDIATE` aqui e um lock
consultivo no inicio de cada transacao (mesma garantia: uma escrita por vez, entao a reserva de tokens segue atomica)."""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.store import SCHEMA

# tabelas com coluna `id` autoincremento: derivadas do proprio SCHEMA (uma tabela nova com id nao pode ficar de fora e devolver lastrowid None)
TABLES_WITH_ID = tuple(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\(\s*id INTEGER PRIMARY KEY AUTOINCREMENT", SCHEMA))
LOCK_KEY = 7_424_001  # lock consultivo do llm-access (serializa transacoes de escrita)


def _ddl() -> str:
    """O mesmo SCHEMA do SQLite, no dialeto do Postgres."""
    s = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    return s


def translate(sql: str) -> str:
    s = sql.replace("%", "%%")                       # % literal (LIKE '[auto]%') nao e placeholder
    s = re.sub(r"(\S+)\s+COLLATE NOCASE", r"LOWER(\1)", s)
    s = re.sub(r"\bLIKE\b", "ILIKE", s)
    s = re.sub(r"\bMAX\(0,", "GREATEST(0,", s)
    s = re.sub(r"^\s*INSERT OR REPLACE INTO", "INSERT INTO", s)
    return s.replace("?", "%s")


class _Cur:
    """Cursor com a cara do sqlite3: execute devolve algo com fetchone/fetchall/lastrowid/rowcount."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql: str, args: tuple = ()):
        s = translate(sql)
        head = s.lstrip().upper()
        wants_id = head.startswith("INSERT INTO") and "RETURNING" not in head and any(re.match(rf"INSERT INTO\s+{t}\b", s.lstrip(), re.I) for t in TABLES_WITH_ID)
        cur = self._c.execute(s + (" RETURNING id" if wants_id else ""), tuple(args))
        return _Res(cur, wants_id)


class _Res:
    def __init__(self, cur, has_id: bool):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.lastrowid = None
        if has_id:
            r = cur.fetchone()
            self.lastrowid = r["id"] if r else None

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []


class PgStore:
    def __init__(self, dsn: str, schema: str = "access"):
        self.dsn, self.schema = dsn, schema
        self._c: psycopg.Connection | None = None
        self._lock = threading.RLock()

    def _conn(self) -> psycopg.Connection:
        if self._c is not None and self._c.closed:
            self._c = None
        if self._c is None:
            c = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)
            c.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            c.execute(f'SET search_path TO "{self.schema}"')
            c.execute(_ddl())
            c.execute("ALTER TABLE keys ADD COLUMN IF NOT EXISTS label TEXT")
            c.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS safety_level BIGINT NOT NULL DEFAULT 1 CHECK(safety_level IN (1,2))")
            c.execute("ALTER TABLE vms ADD COLUMN IF NOT EXISTS external_id TEXT")
            c.execute("ALTER TABLE vms ADD COLUMN IF NOT EXISTS billing_status TEXT")
            c.execute("ALTER TABLE vms ADD COLUMN IF NOT EXISTS next_payment_at DOUBLE PRECISION")
            self._c = c
        return self._c

    @contextmanager
    def tx(self):
        with self._lock:
            c = self._conn()
            c.execute("BEGIN")
            try:
                c.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                yield _Cur(c)
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def query(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._conn().execute(translate(sql), tuple(args)).fetchall()

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

    def drop_schema(self) -> None:
        """So para testes: remove o schema inteiro."""
        with self._lock:
            self._conn().execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._c is not None and not self._c.closed:
                self._c.close()
            self._c = None
