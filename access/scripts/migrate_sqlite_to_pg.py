"""Migra o SQLite do llm-access para o Postgres unificado, uma vez, com conferencia.

Uso:  python scripts/migrate_sqlite_to_pg.py --sqlite /data/access.db --dsn postgresql://usuario:senha@host:5432/banco [--schema access] [--apply]

Sem --apply e so ensaio: le e confere, nao grava nada. Com --apply copia as tabelas mantendo os ids (chaves e pessoas continuam as mesmas),
acerta as sequencias e confere contagens e somas. O SQLite NAO e alterado nem apagado: fica como copia de seguranca.
So roda se o schema de destino estiver vazio (nao mistura dados)."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLES = [  # ordem respeita as chaves estrangeiras
    ("people", ["id", "name", "note", "status", "created_at", "safety_level"], True),
    ("keys", ["id", "person_id", "key_hash", "key_last4", "created_at", "revoked_at"], True),
    ("limits", ["person_id", "session_limit_tokens", "week_limit_tokens", "session_seconds", "week_seconds"], False),
    ("windows", ["person_id", "kind", "start_ts", "end_ts", "used_tokens", "reserved_tokens"], False),
    ("usage_log", ["id", "ts", "person_id", "key_id", "actor", "route", "in_tokens", "out_tokens", "total_tokens", "status", "limits_json"], True),
    ("settings", ["key", "value"], False),
    ("safety_log", ["id", "ts", "classifier", "person_id", "category", "score", "route", "source"], True),
]


def read(path: str) -> dict[str, list[tuple]]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for name, cols, _ in TABLES:
        have = {r[1] for r in c.execute(f"PRAGMA table_info({name})")}
        if not have:
            out[name] = []
            continue
        use = [x for x in cols if x in have]
        rows = c.execute(f"SELECT {','.join(use)} FROM {name}").fetchall()
        # coluna que nao existia no SQLite antigo (ex.: safety_level) entra com o padrao
        default = {"safety_level": 1}
        out[name] = [tuple(dict(zip(use, r)).get(x, default.get(x)) for x in cols) for r in rows]
    return out


def migrate(sqlite_path: str, dsn: str, schema: str, apply: bool) -> dict:
    from app.store_pg import PgStore
    data = read(sqlite_path)
    src = {k: len(v) for k, v in data.items()}
    store = PgStore(dsn, schema)
    report = {"origem": src, "aplicado": False}
    with store.tx() as c:
        existentes = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t, _, _ in TABLES}
    if any(existentes.values()):
        raise SystemExit(f"o schema de destino '{schema}' nao esta vazio ({existentes}); nada foi feito")
    if not apply:
        report["destino_vazio"] = True
        return report
    with store.tx() as c:
        for name, cols, serial in TABLES:
            for row in data[name]:
                c.execute(f"INSERT INTO {name}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", row)
            if serial:
                c.execute(f"SELECT setval(pg_get_serial_sequence('{name}','id'), GREATEST(1, COALESCE((SELECT MAX(id) FROM {name}), 1)))")
    with store.tx() as c:
        dst = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t, _, _ in TABLES}
        soma_src = sum(r[4] for r in data["windows"]) + sum(r[8] for r in data["usage_log"])
        soma_dst = c.execute("SELECT COALESCE(SUM(used_tokens),0) n FROM windows").fetchone()["n"] + c.execute("SELECT COALESCE(SUM(total_tokens),0) n FROM usage_log").fetchone()["n"]
    ok = dst == src and soma_src == soma_dst
    report.update(aplicado=True, destino=dst, soma_origem=soma_src, soma_destino=soma_dst, conferido=ok)
    if not ok:
        raise SystemExit(f"CONFERENCIA FALHOU: origem {src} x destino {dst}; somas {soma_src} x {soma_dst}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--schema", default="access")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print(migrate(a.sqlite, a.dsn, a.schema, a.apply))
