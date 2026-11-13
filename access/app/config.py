"""Configuracao do llm-access: tudo por variavel de ambiente (contrato em DESIGN.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _secret(name: str) -> str:
    """Valor direto (NAME) ou conteudo de arquivo (NAME_FILE), para segredos montados."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    p = os.environ.get(name + "_FILE")
    if p and os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


@dataclass
class Settings:
    db_path: str = "/data/access.db"
    database_url: str = ""         # DATABASE_URL: Postgres unificado da VPS (vazio = SQLite em db_path, usado nos testes)
    database_schema: str = "access"
    upstream_url: str = "http://llm-queue.rt:8000"
    upstream_key: str = ""
    upstream_admin_key: str = ""
    admin_password: str = ""
    session_secret: str = ""       # vazio: segredo aleatorio por boot (as sessoes caem no reinicio)
    qa_mode: bool = False
    owner_key_sha256: str = ""
    platform_key_sha256: str = ""
    panel_dir: str = str(Path(__file__).resolve().parent.parent / "panel" / "dist")
    default_max_tokens: int = 4096
    retention_days: int = 90
    min_window_seconds: int = 3600  # modo QA derruba para 1 s
    default_session_seconds: int = 18000
    default_week_seconds: int = 604800
    login_max_attempts: int = 5
    login_window_s: float = 60.0
    session_ttl_s: float = 12 * 3600
    request_timeout_s: float = 1800.0
    cakto_webhook_secret: str = ""  # CAKTO_WEBHOOK_SECRET: sem ele o webhook responde 503 (nunca fica aberto)
    site_origins: str = "https://brain.rangeltech.net,https://remoto.rangeltech.net"  # SITE_ORIGINS: origens que podem chamar /v1/free pelo navegador
    secrets_key: str = ""           # SECRETS_KEY: chave mestra que cifra credenciais guardadas no banco (Contabo); sem ela nao guarda
    runware_api_key: str = ""       # RUNWARE_API_KEY: Estudio (modelos de ponta na Runware, spec 36). Vazia = Estudio responde "em breve"; a chave nunca sai do servidor
    runware_url: str = "https://api.runware.ai/v1"
    runware_mock: bool = False      # RUNWARE_MOCK=1: responde sem chamar a Runware (QA e desenvolvimento, custo zero)

    @property
    def upstream_base(self) -> str:
        u = self.upstream_url.rstrip("/")
        return u[:-3] if u.endswith("/v1") else u

    @property
    def effective_min_window(self) -> int:
        return 1 if self.qa_mode else self.min_window_seconds

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            db_path=e("ACCESS_DB_PATH", cls.db_path),
            database_url=_secret("DATABASE_URL"),
            database_schema=e("DATABASE_SCHEMA", cls.database_schema),
            upstream_url=e("UPSTREAM_URL", cls.upstream_url),
            upstream_key=_secret("UPSTREAM_KEY"),
            upstream_admin_key=_secret("UPSTREAM_ADMIN_KEY"),
            admin_password=_secret("ADMIN_PASSWORD"),
            session_secret=_secret("SESSION_SECRET"),
            qa_mode=e("ACCESS_QA_MODE", "0").strip().lower() in ("1", "true", "yes", "on"),
            owner_key_sha256=e("OWNER_KEY_SHA256", "").strip().lower(),
            platform_key_sha256=e("PLATFORM_KEY_SHA256", "").strip().lower(),
            panel_dir=e("ACCESS_PANEL_DIR", cls.panel_dir),
            cakto_webhook_secret=_secret("CAKTO_WEBHOOK_SECRET"),
            secrets_key=_secret("SECRETS_KEY"),
            site_origins=e("SITE_ORIGINS", cls.site_origins),
            runware_api_key=_secret("RUNWARE_API_KEY"),
            runware_url=e("RUNWARE_URL", cls.runware_url),
            runware_mock=e("RUNWARE_MOCK", "0").strip().lower() in ("1", "true", "yes", "on"),
        )
