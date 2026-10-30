"""Pecas compartilhadas entre as rotas (a fabrica e os testes injetam relogio e upstream falso)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx
from fastapi import HTTPException

from app.auth import AdminAuth
from app.config import Settings
from app.ledger import Ledger
from app.store import Store


def err(message: str, typ: str = "invalid_request_error", code: str | None = None, **extra) -> dict:
    return {"error": {"message": message, "type": typ, "code": code, **extra}}


def fail(status: int, message: str, typ: str = "invalid_request_error", code: str | None = None, headers: dict | None = None, **extra):
    return HTTPException(status, detail=err(message, typ, code, **extra), headers=headers)


@dataclass
class Ctx:
    s: Settings
    store: Store
    ledger: Ledger
    http: httpx.AsyncClient
    clock: Callable[[], float]
    admin: AdminAuth
