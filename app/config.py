"""Configuracao do llm-queue: tudo por variavel de ambiente, com os padroes da Spec 13 (§8.1)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


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
    # --- servico
    redis_url: str = "redis://redis:6379/5"
    consumers_file: str = "/run/secrets/llm-queue-consumers.json"
    consumers_json: str = ""      # alternativa ao arquivo: JSON na variavel CONSUMERS_JSON (padrao de segredos do vps_rt_infra: so env_file)
    ssh_private_key_b64: str = "" # chave SSH privada em base64 na variavel SSH_PRIVATE_KEY_B64 (gravada em /tmp com 0600 ao subir)
    admin_key_sha256: str = ""
    webhook_url: str = ""
    # --- upstreams (portas LOCAIS dos tuneis SSH; nunca o endereco da Vast, Spec 13 §3.3)
    chat_upstream: str = "http://127.0.0.1:18000/v1"
    embed_upstream: str = "http://127.0.0.1:18001/v1"
    tunnel_mode: str = "ssh"  # ssh | none (none: o upstream ja esta acessivel, usado em testes e dev)
    ssh_key_path: str = ""
    # --- Vast
    vast_api_key: str = ""
    vast_base: str = "https://console.vast.ai/api"
    gpu_name: str = "RTX 3090"
    num_gpus: int = 2
    image: str = "vastai/vllm:v0.30.0-cuda-13.0"
    llm_repo: str = "twolven/Qwen3.8-27B-abliterated-AWQ-MTP"
    embed_repo: str = "Qwen/Qwen3-Embedding-4B"
    embed_mode: str = "off"  # off | gpu. "off" ate o teste de convivencia da Fase 2 (Spec 13 §4.2)
    llm_util: float = 0.92   # 0.68 quando embed_mode=gpu
    embed_util: float = 0.24
    # --- limites de valor (Spec 13 §8.1)
    max_allin_dph: float = 0.45
    max_allin_dph_override: float = 0.0
    override_until: float = 0.0  # epoch; sem prazo, o override nao vale
    max_daily_usd: float = 12.0
    monthly_budget_usd: float = 80.0
    budget_warn_pct: tuple = (50, 80)
    priority_budget_factor: float = 1.2
    max_session_h: float = 8.0
    min_credit_usd: float = 3.0
    max_waste_per_wake_usd: float = 0.25
    max_create_attempts: int = 6
    max_instances: int = 1
    max_wakes_per_day: int = 12
    max_storage_usd_gb_month: float = 0.40
    disk_gb: int = 70
    max_dl_usd_tb: float = 5.0
    min_inet_down: float = 600.0
    min_ram_gb: float = 50.0
    min_ram_gb_relaxed: float = 40.0
    min_reliability: float = 0.96
    min_reliability_relaxed: float = 0.95
    min_disk_bw: float = 1500.0
    min_pcie: float = 6.0
    min_cuda: float = 13.0
    download_gb: float = 40.0
    expected_hours: float = 1.0
    batch_enabled: bool = False
    batch_budget_usd: float = 12.0
    # --- tempos (segundos, Spec 13 §5)
    idle_min: float = 30.0
    stopped_ttl_h: float = 24.0
    stopped_max_usd: float = 0.30   # custo maximo do disco de uma instancia parada: o TTL efetivo e o menor entre stopped_ttl_h e este teto / preco do disco
    watchdog_idle_min: float = 45.0
    reconcile_s: float = 60.0
    poll_s: float = 5.0
    scheduling_timeout_s: float = 60.0
    running_timeout_s: float = 480.0
    ssh_timeout_s: float = 120.0
    health_timeout_s: float = 900.0
    wait_max_s: float = 900.0
    search_retry_s: float = 60.0
    search_window_s: float = 900.0
    keepalive_s: float = 15.0
    bad_host_ttl_h: float = 24.0
    bad_host_hard_ttl_h: float = 168.0
    hedge_enabled: bool = True
    hedge_max_s: float = 600.0
    # --- concorrencia e admissao (Spec 13 §3.1)
    chat_max_inflight: int = 6
    chat_per_consumer: int = 3
    chat_queue_max: int = 30
    embed_max_inflight: int = 8
    embed_queue_max: int = 200
    max_context_tokens: int = 65536
    request_timeout_s: float = 1800.0
    extra: dict = field(default_factory=dict)

    @property
    def effective_max_dph(self) -> float:
        import time
        if self.max_allin_dph_override > self.max_allin_dph and self.override_until > time.time():
            return self.max_allin_dph_override
        return self.max_allin_dph

    @property
    def embed_enabled(self) -> bool:
        return self.embed_mode == "gpu"

    @property
    def effective_llm_util(self) -> float:
        return min(self.llm_util, 0.68) if self.embed_enabled and self.llm_util > 0.68 else self.llm_util

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls(
            redis_url=os.environ.get("REDIS_URL", cls.redis_url),
            consumers_file=os.environ.get("CONSUMERS_FILE", cls.consumers_file),
            consumers_json=_secret("CONSUMERS_JSON"),
            ssh_private_key_b64=_secret("SSH_PRIVATE_KEY_B64"),
            admin_key_sha256=os.environ.get("ADMIN_KEY_SHA256", ""),
            webhook_url=_secret("GOOGLE_CHAT_WEBHOOK"),
            chat_upstream=os.environ.get("CHAT_UPSTREAM", cls.chat_upstream),
            embed_upstream=os.environ.get("EMBED_UPSTREAM", cls.embed_upstream),
            tunnel_mode=os.environ.get("TUNNEL_MODE", cls.tunnel_mode),
            ssh_key_path=os.environ.get("SSH_KEY_PATH", ""),
            vast_api_key=_secret("VAST_API_KEY"),
            vast_base=os.environ.get("VAST_BASE", cls.vast_base),
            embed_mode=os.environ.get("GPU_EMBED_MODE", cls.embed_mode),
            image=os.environ.get("GPU_IMAGE", cls.image),
            llm_repo=os.environ.get("GPU_LLM_REPO", cls.llm_repo),
            embed_repo=os.environ.get("GPU_EMBED_REPO", cls.embed_repo),
            llm_util=_f("GPU_LLM_UTIL", cls.llm_util),
            embed_util=_f("GPU_EMBED_UTIL", cls.embed_util),
            max_allin_dph=_f("GPU_MAX_ALLIN_DPH_USD", cls.max_allin_dph),
            max_allin_dph_override=_f("GPU_MAX_ALLIN_DPH_USD_OVERRIDE", 0.0),
            override_until=_f("GPU_OVERRIDE_UNTIL", 0.0),
            max_daily_usd=_f("GPU_MAX_DAILY_USD", cls.max_daily_usd),
            monthly_budget_usd=_f("GPU_MONTHLY_BUDGET_USD", cls.monthly_budget_usd),
            max_session_h=_f("GPU_MAX_SESSION_H", cls.max_session_h),
            min_credit_usd=_f("GPU_MIN_CREDIT_USD", cls.min_credit_usd),
            max_waste_per_wake_usd=_f("GPU_MAX_WASTE_PER_WAKE_USD", cls.max_waste_per_wake_usd),
            max_create_attempts=_i("GPU_MAX_CREATE_ATTEMPTS", cls.max_create_attempts),
            max_wakes_per_day=_i("GPU_MAX_WAKES_PER_DAY", cls.max_wakes_per_day),
            max_storage_usd_gb_month=_f("GPU_MAX_STORAGE_USD_GB_MONTH", cls.max_storage_usd_gb_month),
            disk_gb=_i("GPU_DISK_GB", cls.disk_gb),
            max_dl_usd_tb=_f("GPU_MAX_DL_USD_TB", cls.max_dl_usd_tb),
            idle_min=_f("GPU_IDLE_MIN", cls.idle_min),
            stopped_ttl_h=_f("GPU_STOPPED_TTL_H", cls.stopped_ttl_h),
            stopped_max_usd=_f("GPU_STOPPED_MAX_USD", cls.stopped_max_usd),
            batch_enabled=_b("GPU_BATCH_ENABLED", cls.batch_enabled),
            batch_budget_usd=_f("GPU_BATCH_BUDGET_USD", cls.batch_budget_usd),
            hedge_enabled=_b("GPU_HEDGE_ENABLED", cls.hedge_enabled),
            keepalive_s=_f("KEEPALIVE_S", cls.keepalive_s),
            chat_max_inflight=_i("CHAT_MAX_INFLIGHT", cls.chat_max_inflight),
            embed_max_inflight=_i("EMBED_MAX_INFLIGHT", cls.embed_max_inflight),
        )
        return s
