"""Erros de banco aceitos como "banco indisponivel" (SQLite e Postgres)."""
import sqlite3

try:  # o Postgres so existe quando DATABASE_URL esta ligada; o pacote vem nas dependencias
    import psycopg
    DB_ERRORS: tuple = (sqlite3.Error, psycopg.Error)
except ImportError:  # pragma: no cover
    DB_ERRORS = (sqlite3.Error,)
