"""Migracao SQLite -> Postgres unificado. Roda so com TEST_DATABASE_URL (Postgres de teste)."""
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="precisa de TEST_DATABASE_URL")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def sqlite_antigo(path: str) -> None:
    """Banco no formato ANTIGO (sem safety_level), como esta em producao hoje."""
    from app.store import SCHEMA
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO people(id,name,note,status,created_at) VALUES(7,'Ana','n','active',1.5),(9,'Beto','','suspended',2.5)")
    c.execute("INSERT INTO keys(id,person_id,key_hash,key_last4,created_at) VALUES(3,7,'h1','abcd',1.0),(4,9,'h2','wxyz',2.0)")
    c.execute("INSERT INTO limits VALUES(7,1000,NULL,18000,604800),(9,NULL,5000,18000,604800)")
    c.execute("INSERT INTO windows VALUES(7,'session',1.0,99.0,120,30)")
    c.execute("INSERT INTO usage_log(id,ts,person_id,key_id,actor,route,in_tokens,out_tokens,total_tokens,status) VALUES(5,10.0,7,3,NULL,'chat',10,20,30,'ok')")
    c.execute("INSERT INTO settings VALUES('admin_password_hash','x')")
    c.commit()
    c.close()


def test_migra_confere_e_continua_funcionando(tmp_path):
    from migrate_sqlite_to_pg import migrate
    from app.store_pg import PgStore
    origem = str(tmp_path / "antigo.db")
    sqlite_antigo(origem)
    schema = "mig_" + uuid.uuid4().hex[:8]
    try:
        ensaio = migrate(origem, DSN, schema, apply=False)
        assert ensaio["aplicado"] is False and ensaio["origem"]["people"] == 2
        r = migrate(origem, DSN, schema, apply=True)
        assert r["conferido"] and r["destino"]["people"] == 2 and r["destino"]["keys"] == 2 and r["soma_origem"] == r["soma_destino"] == 150
        s = PgStore(DSN, schema)
        assert s.one("SELECT safety_level FROM people WHERE id=7")["safety_level"] == 1  # quem ja existia segue sem trava
        assert s.one("SELECT key_last4 FROM keys WHERE id=3")["key_last4"] == "abcd"   # os ids (e as chaves) continuam os mesmos
        with s.tx() as c:  # a sequencia continua depois do maior id: novo cadastro nao colide
            novo = c.execute("INSERT INTO people(name,note,created_at) VALUES('Novo','',3.0)").lastrowid
        assert novo == 10
        with pytest.raises(SystemExit):  # nao mistura com destino ja preenchido
            migrate(origem, DSN, schema, apply=True)
        s.close()
    finally:
        PgStore(DSN, schema).drop_schema()
