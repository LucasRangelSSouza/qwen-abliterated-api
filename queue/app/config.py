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
    tool_parser: str = "qwen3_xml"  # o Hermes envia tools com tool_choice=auto: o vLLM exige --enable-auto-tool-choice e um parser (medido em 30/10)
    drive_remote: str = ""      # ex.: gdrive:qwen-cache (Spec 14 §5b): origem dos pesos antes do Hugging Face; vazio = so Hugging Face
    rclone_config_b64: str = ""  # rclone.conf em base64 (segredo; vai no env da instancia, risco aceito pelo dono)
    stt_enabled: bool = False  # STT_ENABLED: transcricao de voz (ditado) no processador da maquina do LLM, porta 18002 (tunel proprio)
    stt_upstream: str = "http://127.0.0.1:18002"
    stt_max_mb: int = 25
    stt_threads: int = 16
    stt_concurrency: int = 2
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
    auto_shutdown: bool = False     # valor inicial do interruptor (GPU_AUTO_SHUTDOWN); em execucao vale o que estiver no Redis (/admin/gpu/config)
    reserved: bool = False          # valor inicial do modo "reservada" (GPU_RESERVED)
    static_instance_id: int = 0     # GPU_STATIC_INSTANCE_ID: instancia fixa 24/7 (0 = desligado); o controlador nunca cria, para nem destroi
    static_retry_s: float = 15.0    # modo fixo: primeira espera entre tentativas de reconectar; dobra ate static_retry_max_s
    static_retry_max_s: float = 300.0
    # modo fixo: vLLM morto (EngineDeadError) nao volta sozinho; o controlador roda este comando por SSH (so o processo; a instancia nunca para).
    # O script tem guarda contra segunda instancia. Vazio desliga a auto-recuperacao.
    static_llm_start_cmd: str = "export LLM_UTIL=0.90 MAX_LEN=131072 MAX_SEQS=32 MAX_BATCHED=16384; bash /workspace/start_vllm.sh"
    static_llm_restart_min_s: float = 600.0
    # instala o remote/start_vllm.sh versionado na maquina (por SSH, idempotente) antes de cada partida do vLLM; 0 desliga
    static_llm_install: bool = True
    stopped_ttl_h: float = 24.0
    stopped_max_usd: float = 0.30   # custo maximo do disco de uma instancia parada: o TTL efetivo e o menor entre stopped_ttl_h e este teto / preco do disco
    watchdog_idle_min: float = 45.0
    reconcile_s: float = 60.0
    poll_s: float = 5.0
    scheduling_timeout_s: float = 60.0
    running_timeout_s: float = 480.0
    ssh_timeout_s: float = 120.0
    health_timeout_s: float = 900.0
    connect_retries: int = 1     # host que nao abre SSH nem fica saudavel: troca de oferta no mesmo ciclo (Spec 14, G2) antes de desistir
    wait_max_s: float = 1500.0   # um host que falha ao baixar a imagem custa 8 min antes do proximo; medido: cold start de 18 min no pior caso
    search_retry_s: float = 60.0
    search_window_s: float = 900.0
    keepalive_s: float = 15.0
    notice_enabled: bool = True          # aviso de espera dentro do stream (Spec 14 §3)
    notice_every_s: float = 60.0
    notice_fields: tuple = ("reasoning", "reasoning_content")  # campos do delta que carregam o aviso; o vLLM usa um dos dois conforme a versao
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
    upstream_retry_s: float = 150.0  # UPSTREAM_RETRY_S: queda curta da ligacao com a GPU (tunel/host) antes de qualquer byte: segura o pedido e tenta de novo por este tempo em vez de devolver erro
    presence_penalty: float = 0.5  # PRESENCE_PENALTY: padrao injetado no chat quando o cliente nao manda o seu. Medido em 07/11/2026 (codigo longo via ferramenta, 6 pedidos por valor): 1.5 estourou 6000 tokens em 3/6 e so 2/6 compilaram; 0 e 0.5 deram 6/6. 0 desliga
    repetition_penalty: float = 0.0  # REPETITION_PENALTY: desligado por padrao (1.1 quebrou geracao de codigo em modo agente); o cliente ainda pode mandar o seu
    request_timeout_s: float = 1800.0
    stream_idle_timeout_s: float = 420.0  # STREAM_IDLE_TIMEOUT_S: stream sem nenhum byte do vLLM por este tempo e abortado (o parser qwen3_xml so entrega a chamada de ferramenta quando completa: escrita de arquivo grande fica 2 a 4 min sem bytes)
    # --- midia (imagem; ComfyUI numa GPU fixa propria, alcancada por tunel SSH)
    media_instance_id: int = 0       # MEDIA_INSTANCE_ID: instancia Vast da GPU de midia (0 = sem midia)
    media_local_port: int = 18188    # porta local do tunel ate o ComfyUI (8188 na maquina)
    media_queue_max: int = 30
    media_per_consumer: int = 4
    media_job_timeout_s: float = 300.0
    media_sync_wait_s: float = 150.0  # quanto o pedido sincrono espera antes de devolver 202 com o id
    media_poll_s: float = 0.5
    media_keepalive_s: float = 20.0   # vigia: se o tunel cair e a maquina estiver de pe, reabre sozinho
    media_ckpt: str = "flux1-schnell-fp8.safetensors"
    # video desligado por padrao: na GPU de midia de 16 GB de RAM, Flux + T5 + LTX nao cabem juntos e o ComfyUI morria (137) logo depois do video,
    # derrubando a imagem dos clientes. Ligue com MEDIA_VIDEO_ENABLED=true so numa maquina com RAM suficiente (32 GB ou mais).
    media_video_enabled: bool = False
    # --- worker de video sob demanda (serverless proprio): aluga uma RTX PRO 6000 na Vast quando chega pedido de video de alta qualidade
    # (Wan 2.2 14B), instala e roda os pedidos em fila, e DESTROI a maquina depois de `worker_idle_s` sem pedido. Sem pedido = sem custo.
    worker_enabled: bool = False        # WORKER_ENABLED
    worker_local_port: int = 18189      # porta local do tunel ate o ComfyUI do worker (8188 na maquina)
    worker_gpu_names: tuple = ("RTX PRO 6000 S", "RTX PRO 6000 WS")  # WORKER_GPU_NAMES (a Max-Q e limitada em potencia: fica de fora)
    worker_image: str = "pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel"
    worker_disk_gb: int = 120           # os modelos somam ~81 GB
    worker_max_dph: float = 1.80        # WORKER_MAX_DPH_USD: teto do preco TOTAL por hora (GPU + disco); acima disso nao aluga
    worker_min_down_mbps: int = 1000    # rede lenta transforma 81 GB de modelos em partida de 30 min: so aceita host rapido
    worker_min_reliability: float = 0.95
    worker_idle_s: float = 600.0        # WORKER_IDLE_S: sem pedido por este tempo, destroi a maquina (pedidos seguidos aproveitam a maquina quente)
    worker_max_age_h: float = 8.0       # trava de seguranca: maquina com mais que isso de vida e destruida assim que ficar ociosa
    worker_start_timeout_s: float = 1500.0  # tempo maximo para alugar + instalar; passou disso tenta outra oferta
    worker_wait_s: float = 3600.0       # quanto o pedido espera por uma maquina (ofertas escassas) antes de falhar
    worker_min_credit_usd: float = 3.0  # saldo abaixo disso: nao aluga
    worker_job_timeout_s: float = 2400.0
    hf_token: str = ""                                    # HF_TOKEN: leitura do repositorio PRIVADO de pesos (troca de rosto); vazio = so repositorios publicos
    # classe PEQUENA (3090/4090 de ~US$ 0,3/h): imagem, voz, dublagem e troca de rosto. Substitui a GPU de midia fixa (que ficava ligada 24 h com ~2% de uso).
    worker_small_enabled: bool = False                    # WORKER_SMALL_ENABLED
    worker_small_gpu_names: tuple = ("RTX 3090", "RTX 4090")
    worker_small_max_dph: float = 0.65                    # WORKER_SMALL_MAX_DPH_USD: preco TOTAL (GPU + disco) por hora
    worker_small_disk_gb: int = 170                       # Flux 17 + troca de rosto ~55 + voz ~25 + ComfyUI e ambientes
    worker_small_min_down_mbps: int = 800
    worker_small_min_ram_gb: float = 64.0                 # o H3 le os pesos da RAM do host
    worker_small_idle_s: float = 900.0                    # tem custo baixo: segura a maquina mais tempo para pedidos seguidos
    worker_small_local_port: int = 18190
    worker_small_cold_start_s: int = 360
    extra: dict = field(default_factory=dict)

    @property
    def effective_max_dph(self) -> float:
        import time
        if self.max_allin_dph_override > self.max_allin_dph and self.override_until > time.time():
            return self.max_allin_dph_override
        return self.max_allin_dph

    @property
    def static_mode(self) -> bool:
        return self.static_instance_id > 0

    @property
    def media_enabled(self) -> bool:
        return self.media_instance_id > 0

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
            stt_enabled=os.environ.get("STT_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"),
            stt_upstream=os.environ.get("STT_UPSTREAM", cls.stt_upstream),
            stt_threads=int(os.environ.get("STT_THREADS", "").strip() or cls.stt_threads),
            tunnel_mode=os.environ.get("TUNNEL_MODE", cls.tunnel_mode),
            ssh_key_path=os.environ.get("SSH_KEY_PATH", ""),
            vast_api_key=_secret("VAST_API_KEY"),
            vast_base=os.environ.get("VAST_BASE", cls.vast_base),
            embed_mode=os.environ.get("GPU_EMBED_MODE", cls.embed_mode),
            drive_remote=os.environ.get("GPU_DRIVE_REMOTE", ""),
            rclone_config_b64=_secret("RCLONE_CONFIG_B64"),
            tool_parser=os.environ.get("GPU_TOOL_PARSER", cls.tool_parser),
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
            auto_shutdown=_b("GPU_AUTO_SHUTDOWN", cls.auto_shutdown),
            reserved=_b("GPU_RESERVED", cls.reserved),
            static_instance_id=int(os.environ.get("GPU_STATIC_INSTANCE_ID", "").strip() or 0),
            media_instance_id=int(os.environ.get("MEDIA_INSTANCE_ID", "").strip() or 0),
            media_local_port=_i("MEDIA_LOCAL_PORT", cls.media_local_port),
            media_queue_max=_i("MEDIA_QUEUE_MAX", cls.media_queue_max),
            media_per_consumer=_i("MEDIA_PER_CONSUMER", cls.media_per_consumer),
            media_job_timeout_s=_f("MEDIA_JOB_TIMEOUT_S", cls.media_job_timeout_s),
            media_sync_wait_s=_f("MEDIA_SYNC_WAIT_S", cls.media_sync_wait_s),
            media_ckpt=os.environ.get("MEDIA_CKPT", cls.media_ckpt),
            media_video_enabled=os.environ.get("MEDIA_VIDEO_ENABLED", "false").strip().lower() in ("1", "true", "yes", "sim"),
            worker_enabled=_b("WORKER_ENABLED", cls.worker_enabled),
            worker_local_port=_i("WORKER_LOCAL_PORT", cls.worker_local_port),
            worker_gpu_names=tuple(x.strip() for x in os.environ.get("WORKER_GPU_NAMES", ",".join(cls.worker_gpu_names)).split(",") if x.strip()),
            worker_image=os.environ.get("WORKER_IMAGE", cls.worker_image),
            worker_disk_gb=_i("WORKER_DISK_GB", cls.worker_disk_gb),
            worker_max_dph=_f("WORKER_MAX_DPH_USD", cls.worker_max_dph),
            worker_min_down_mbps=_i("WORKER_MIN_DOWN_MBPS", cls.worker_min_down_mbps),
            worker_min_reliability=_f("WORKER_MIN_RELIABILITY", cls.worker_min_reliability),
            worker_idle_s=_f("WORKER_IDLE_S", cls.worker_idle_s),
            worker_max_age_h=_f("WORKER_MAX_AGE_H", cls.worker_max_age_h),
            worker_start_timeout_s=_f("WORKER_START_TIMEOUT_S", cls.worker_start_timeout_s),
            worker_wait_s=_f("WORKER_WAIT_S", cls.worker_wait_s),
            worker_min_credit_usd=_f("WORKER_MIN_CREDIT_USD", cls.worker_min_credit_usd),
            worker_job_timeout_s=_f("WORKER_JOB_TIMEOUT_S", cls.worker_job_timeout_s),
            hf_token=_secret("HF_TOKEN"),
            worker_small_enabled=_b("WORKER_SMALL_ENABLED", cls.worker_small_enabled),
            worker_small_gpu_names=tuple(x.strip() for x in os.environ.get("WORKER_SMALL_GPU_NAMES", ",".join(cls.worker_small_gpu_names)).split(",") if x.strip()),
            worker_small_max_dph=_f("WORKER_SMALL_MAX_DPH_USD", cls.worker_small_max_dph),
            worker_small_disk_gb=_i("WORKER_SMALL_DISK_GB", cls.worker_small_disk_gb),
            worker_small_min_down_mbps=_i("WORKER_SMALL_MIN_DOWN_MBPS", cls.worker_small_min_down_mbps),
            worker_small_min_ram_gb=_f("WORKER_SMALL_MIN_RAM_GB", cls.worker_small_min_ram_gb),
            worker_small_idle_s=_f("WORKER_SMALL_IDLE_S", cls.worker_small_idle_s),
            worker_small_cold_start_s=_i("WORKER_SMALL_COLD_START_S", cls.worker_small_cold_start_s),
            static_retry_s=_f("GPU_STATIC_RETRY_S", cls.static_retry_s),
            static_retry_max_s=_f("GPU_STATIC_RETRY_MAX_S", cls.static_retry_max_s),
            static_llm_start_cmd=os.environ.get("GPU_STATIC_LLM_START_CMD", cls.static_llm_start_cmd),
            static_llm_restart_min_s=_f("GPU_STATIC_LLM_RESTART_MIN_S", cls.static_llm_restart_min_s),
            static_llm_install=os.environ.get("GPU_STATIC_LLM_INSTALL", "1").strip().lower() not in ("0", "false", "no"),
            stopped_ttl_h=_f("GPU_STOPPED_TTL_H", cls.stopped_ttl_h),
            stopped_max_usd=_f("GPU_STOPPED_MAX_USD", cls.stopped_max_usd),
            wait_max_s=_f("GPU_WAIT_MAX_S", cls.wait_max_s),
            running_timeout_s=_f("GPU_RUNNING_TIMEOUT_S", cls.running_timeout_s),
            connect_retries=_i("GPU_CONNECT_RETRIES", cls.connect_retries),
            batch_enabled=_b("GPU_BATCH_ENABLED", cls.batch_enabled),
            batch_budget_usd=_f("GPU_BATCH_BUDGET_USD", cls.batch_budget_usd),
            hedge_enabled=_b("GPU_HEDGE_ENABLED", cls.hedge_enabled),
            keepalive_s=_f("KEEPALIVE_S", cls.keepalive_s),
            notice_enabled=_b("NOTICE_ENABLED", cls.notice_enabled),
            notice_every_s=_f("NOTICE_EVERY_S", cls.notice_every_s),
            upstream_retry_s=_f("UPSTREAM_RETRY_S", cls.upstream_retry_s),
            presence_penalty=_f("PRESENCE_PENALTY", cls.presence_penalty),
            repetition_penalty=_f("REPETITION_PENALTY", cls.repetition_penalty),
            notice_fields=tuple(x.strip() for x in os.environ.get("NOTICE_FIELDS", ",".join(cls.notice_fields)).split(",") if x.strip()),
            chat_max_inflight=_i("CHAT_MAX_INFLIGHT", cls.chat_max_inflight),
            chat_per_consumer=_i("CHAT_PER_CONSUMER", cls.chat_per_consumer),
            chat_queue_max=_i("CHAT_QUEUE_MAX", cls.chat_queue_max),
            embed_max_inflight=_i("EMBED_MAX_INFLIGHT", cls.embed_max_inflight),
            embed_queue_max=_i("EMBED_QUEUE_MAX", cls.embed_queue_max),
            max_context_tokens=_i("MAX_CONTEXT_TOKENS", cls.max_context_tokens),
            stream_idle_timeout_s=_f("STREAM_IDLE_TIMEOUT_S", cls.stream_idle_timeout_s),
        )
        return s
