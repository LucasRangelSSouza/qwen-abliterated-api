"""llm-access: porta publica da API de IA (Spec 14 §5). Chave por pessoa, limites de janela, painel em /admin; repassa ao llm-queue."""
from __future__ import annotations

import time
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from app import admin, enroll, proxy
from app import vms_routes  # noqa: F401  (registra as rotas do painel de VMs em admin.api ANTES do include_router)
from app.auth import AdminAuth
from app.config import Settings
from app.ctx import Ctx
from app.ledger import Ledger
from app.store import Store


async def _rotina_vms(ctx) -> None:
    """De 10 em 10 minutos: atualiza as VMs de clientes e desliga/religa conforme o pagamento. So roda com a Contabo configurada."""
    from app import vms
    while True:
        await asyncio.sleep(600)
        try:
            if vms.credenciais_configuradas(ctx):
                await vms.provisionar_pagas(ctx)
                await vms.sincronizar(ctx)
                await vms.varredura(ctx)
        except Exception:  # noqa: BLE001  a rotina nunca derruba o servico
            pass


def create_app(s: Settings | None = None, *, http: httpx.AsyncClient | None = None, clock=time.time) -> FastAPI:
    s = s or Settings.from_env()
    if s.database_url:
        from app.store_pg import PgStore  # Postgres unificado (producao)
        store = PgStore(s.database_url, s.database_schema)
    else:
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
        tarefa = asyncio.create_task(_rotina_vms(ctx))
        try:
            yield
        finally:
            tarefa.cancel()
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

    @app.get("/", include_in_schema=False)
    async def raiz():
        """Quem abre o endereco no navegador cai no painel (as rotas da API continuam em /v1)."""
        return RedirectResponse("/admin/", status_code=307)

    from app import accounts_routes  # registra o CRM no router do painel antes dele ser incluido
    from app import free
    from app import diag  # relatorio de falha de instalacao: registra GET /admin/api/diag antes do include_router(admin.api)
    from fastapi.middleware.cors import CORSMiddleware
    # o teste grátis é chamado do site de vendas (outro domínio): só as origens do site, só as rotas públicas /v1/free
    app.add_middleware(CORSMiddleware, allow_origins=[o for o in (s.site_origins or '').split(',') if o], allow_methods=['GET', 'POST', 'OPTIONS'], allow_headers=['content-type', 'authorization'], max_age=3600)
    app.include_router(proxy.router)
    app.include_router(accounts_routes.router)
    app.include_router(free.router)
    app.include_router(diag.router)
    from app import versao
    app.include_router(versao.router)
    app.include_router(enroll.router)
    app.include_router(admin.api)
    app.include_router(admin.static)  # por ultimo: o catch-all /admin/{path} nao pode engolir /admin/api
    return app


app = None  # criado por uvicorn --factory app.main:create_app
