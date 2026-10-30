"""llm-access: porta publica da API de IA (Spec 14 §5). Chave por pessoa, limites de janela, painel em /admin; repassa ao llm-queue."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app import admin, proxy
from app.auth import AdminAuth
from app.config import Settings
from app.ctx import Ctx
from app.ledger import Ledger
from app.store import Store


def create_app(s: Settings | None = None, *, http: httpx.AsyncClient | None = None, clock=time.time) -> FastAPI:
    s = s or Settings.from_env()
    store = Store(s.db_path)
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(s.request_timeout_s, connect=10.0))
    ctx = Ctx(s, store, Ledger(store, clock, s.retention_days), client, clock,
              AdminAuth(s.admin_password, s.session_secret, store, clock, s.session_ttl_s, s.login_max_attempts, s.login_window_s))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            ctx.ledger.purge()  # retencao de 90 dias
        except Exception:  # noqa: BLE001  banco fora do ar nao impede subir (chaves OWNER/PLATFORM)
            pass
        try:
            yield
        finally:
            if http is None:
                await client.aclose()
            store.close()

    app = FastAPI(title="llm-access", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc: HTTPException):
        body = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else {"error": {"message": str(exc.detail), "type": "invalid_request_error", "code": None}}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(proxy.router)
    app.include_router(admin.api)
    app.include_router(admin.static)  # por ultimo: o catch-all /admin/{path} nao pode engolir /admin/api
    return app


app = None  # criado por uvicorn --factory app.main:create_app
