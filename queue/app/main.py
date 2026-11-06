"""llm-queue: API compativel com OpenAI (chat e embeddings) na frente de uma GPU sob demanda (Spec 13).
Cabecalhos saem na hora; durante o cold start vao comentarios SSE (stream) ou espacos (JSON) a cada KEEPALIVE_S, para nao
estourar o STREAM_STALL_TIMEOUT_MS do llm-gateway nem o timeout do undici (Spec 13 §7)."""
from __future__ import annotations

import asyncio
import collections
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app import metrics as mx, notice
from app.alerts import Alerts
from app.config import Settings
from app.gpu.controller import READY, GPUController, GPUUnavailable
from app.gpu.ledger import Ledger
from app.gpu.tunnel import NoTunnel, SshTunnel
from app.gpu.vast import RealVast
from app.media import EXT, Media, MediaError, MediaUnavailable
from app.scheduler import Lane, QueueFull

log = logging.getLogger("llm-queue")

REQS = Counter("llmq_requests_total", "requisicoes", ["consumer", "kind", "code"])
WAIT = Histogram("llmq_wait_seconds", "espera ate o inicio do servico", ["kind"], buckets=(1, 5, 15, 60, 180, 360, 720, 900))
GPU_STATE = Gauge("llmq_gpu_state", "estado da GPU (1 = atual)", ["state"])
QUEUE = Gauge("llmq_queue_waiting", "itens esperando", ["kind"])
INFL = Gauge("llmq_inflight", "em voo", ["kind"])


def _err(message: str, typ: str = "server_error", code: str | None = None) -> dict:
    return {"error": {"message": message, "type": typ, "code": code}}



IMAGE_TOKENS = 1500  # custo fixo por imagem: o base64 nao pode entrar na conta de caracteres (1 MB viraria 350 mil tokens)


def approx_chat_tokens(messages) -> int:
    """Estimativa grosseira de tokens de um chat: caracteres/3 no texto e custo fixo por imagem."""
    n = 0
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            n += len(json.dumps(m, ensure_ascii=False)) // 3
            continue
        c = m.get("content")
        if isinstance(c, str):
            n += len(c) // 3
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") in ("image_url", "input_image", "image"):
                    n += IMAGE_TOKENS
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    n += len(part["text"]) // 3
                else:
                    n += len(json.dumps(part, ensure_ascii=False)) // 3
        elif c is not None:
            n += len(json.dumps(c, ensure_ascii=False)) // 3
        n += len(json.dumps({k: v for k, v in m.items() if k != "content"}, ensure_ascii=False)) // 3
    return n

class App:
    """Agrupa as pecas (usado pela fabrica e pelos testes, que injetam fakes)."""
    def __init__(self, s: Settings, controller: GPUController, redis, http: httpx.AsyncClient, alerts: Alerts):
        self.s, self.ctl, self.r, self.http, self.alerts = s, controller, redis, http, alerts
        self.chat = Lane("chat", s.chat_max_inflight, s.chat_queue_max, s.chat_per_consumer)
        self.embed = Lane("embed", s.embed_max_inflight, s.embed_queue_max, s.embed_max_inflight)
        self.consumers = self._load_consumers()
        self.recent: collections.deque = collections.deque(maxlen=60)  # ultimos pedidos encerrados (so metadados)
        self.media: Media | None = None

    def _load_consumers(self) -> dict:
        try:
            raw = json.loads(self.s.consumers_json) if self.s.consumers_json else json.load(open(self.s.consumers_file, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        return {c["key_sha256"]: c for c in raw}

    def auth(self, request: Request) -> dict:
        h = request.headers.get("authorization", "")
        key = h[7:].strip() if h.lower().startswith("bearer ") else ""
        digest = hashlib.sha256(key.encode()).hexdigest()
        for d, c in self.consumers.items():
            if hmac.compare_digest(d, digest):
                return c
        raise HTTPException(401, detail=_err("chave de API invalida", "invalid_request_error", "invalid_api_key"))

    def lane_for(self, kind: str) -> Lane:
        return self.chat if kind == "chat" else self.embed

    def upstream(self, kind: str) -> str:
        return (self.s.chat_upstream if kind == "chat" else self.s.embed_upstream).rstrip("/")


def create_app(s: Settings | None = None, *, controller: GPUController | None = None, redis=None, http: httpx.AsyncClient | None = None,
               consumers: dict | None = None, media: Media | None = None) -> FastAPI:
    s = s or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if s.ssh_private_key_b64 and not s.ssh_key_path:
            s.ssh_key_path = _write_key(s.ssh_private_key_b64)
        if redis is not None:
            r = redis
        elif s.redis_url.startswith("fakeredis"):  # so desenvolvimento local
            import fakeredis.aioredis
            r = fakeredis.aioredis.FakeRedis()
        else:
            r = aioredis.from_url(s.redis_url)
        alerts = Alerts(s.webhook_url)
        client = http or httpx.AsyncClient(timeout=httpx.Timeout(s.request_timeout_s, connect=10.0))
        ctl = controller
        if ctl is None:
            vast = RealVast(s.vast_api_key, s.vast_base, s.gpu_name, s.num_gpus)
            tun = NoTunnel() if s.tunnel_mode == "none" else SshTunnel(s.ssh_key_path, _forwards(s))
            ctl = GPUController(s, vast, tun, Ledger(r), alerts, r, health_probe=_make_probe(s, client))
            ctl.start()
        app.state.q = App(s, ctl, r, client, alerts)
        if media is not None:
            app.state.q.media = media
        elif s.media_enabled:
            mvast = RealVast(s.vast_api_key, s.vast_base, "RTX 3090", 1)
            mtun = NoTunnel() if s.tunnel_mode == "none" else SshTunnel(s.ssh_key_path, [(s.media_local_port, 8188)])
            app.state.q.media = Media(s, mvast, mtun, r, client)
            app.state.q.media.start()
        if consumers is not None:
            app.state.q.consumers = consumers
        try:
            yield
        finally:
            if media is None and app.state.q.media is not None:
                await app.state.q.media.stop()
            if controller is None:
                await ctl.stop_loop()
            if http is None:
                await client.aclose()

    app = FastAPI(title="llm-queue", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/v1/models")
    async def models(request: Request):
        q: App = request.app.state.q
        q.auth(request)
        data = [{"id": "qwen-abliterated", "object": "model", "owned_by": "llm-queue"}]
        if q.s.embed_enabled:
            data.append({"id": "qwen-embedding", "object": "model", "owned_by": "llm-queue"})
        return {"object": "list", "data": data}

    @app.get("/v1/gpu/status")
    async def gpu_status(request: Request):
        q: App = request.app.state.q
        q.auth(request)
        return await _status(q)

    @app.get("/admin/gpu/status")
    async def admin_status(request: Request):
        return await _status(_admin(request))

    async def _status(q: App) -> dict:
        st = q.ctl.status()
        led = await q.ctl.ledger.snapshot()
        st.update({"limits": {"idle_min": st["config"]["idle_min"], "max_daily_usd": q.s.max_daily_usd, "monthly_budget_usd": q.s.monthly_budget_usd,
                              "max_allin_dph": q.s.effective_max_dph, "min_credit_usd": q.s.min_credit_usd, "wait_max_s": q.s.wait_max_s,
                              "stopped_ttl_h": q.s.stopped_ttl_h, "embed_mode": q.s.embed_mode},
                   "queue": {"chat": q.chat.stats(), "embed": q.embed.stats()},
                   "spend": {"month_usd": led["month_usd"], "day_usd": led["day_usd"], "month_cap_usd": q.s.monthly_budget_usd,
                             "remaining_usd": round(max(0.0, q.s.monthly_budget_usd - led["month_usd"]), 4),
                             "max_allin_dph": q.s.effective_max_dph}})
        return st

    @app.get("/admin/gpu/jobs")
    async def admin_jobs(request: Request):
        """Fila de pedidos para o painel: quem espera, quem esta rodando e os ultimos encerrados. So metadados (sem prompt nem resposta)."""
        q = _admin(request)
        now = time.time()
        gpu_ready = q.ctl.state == READY

        def row(t, state):
            return {"id": t.seq, "state": state, "kind": t.kind, "consumer": t.consumer, "end_user": t.end_user, "cls": t.cls,
                    "enq": t.t_enq, "waited_s": round(((t.t_start or now) - t.t_enq), 1), "running_s": round(now - t.t_start, 1) if t.t_start else None}
        waiting, running = [], []
        for lane in (q.chat, q.embed):
            waiting += [row(t, "na_fila" if gpu_ready else "aguardando_gpu") for t in lane.waiting]
            running += [row(t, "executando") for t in lane.running]
        waiting.sort(key=lambda r: r["enq"])
        return {"gpu_state": q.ctl.state, "lanes": {"chat": q.chat.stats(), "embed": q.embed.stats()}, "waiting": waiting, "running": running,
                "recent": list(q.recent)}

    # ---- midia (imagem): fila propria, uma por vez, na GPU de midia
    def _media(q: App) -> Media:
        if q.media is None:
            raise HTTPException(503, detail=_err("Este recurso não está disponível no momento.", "service_unavailable", "media_disabled"))
        return q.media

    @app.post("/v1/videos/generations")
    async def videos_generations(request: Request):
        """Video: leva de 1 a varios minutos, entao a resposta padrao e 202 com o id (acompanhe em /v1/jobs/{id}). Entra na fila como `batch`: imagem passa na frente."""
        return await _generate(request, "video")

    @app.post("/v1/images/generations")
    async def images_generations(request: Request):
        return await _generate(request, "image")

    async def _generate(request: Request, kind: str):
        q: App = request.app.state.q
        c = q.auth(request)
        m = _media(q)
        try:
            body = await request.json()
            assert isinstance(body, dict)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, detail=_err("corpo invalido", "invalid_request_error", "invalid_body"))
        cls = request.headers.get("x-queue-class", "interactive")
        end_user = request.headers.get("x-end-user", "")[:60]
        try:
            cls = "batch" if kind == "video" else (cls if cls in ("interactive", "batch") else "interactive")
            job = await m.submit(c["name"], end_user, body, cls, kind)
        except QueueFull as e:
            raise HTTPException(429, detail=_err("fila de midia cheia", "rate_limit_error", "queue_full"), headers={"Retry-After": str(e.retry_after)})
        except MediaError as e:
            raise HTTPException(422, detail=_err(str(e), "invalid_request_error", "invalid_media_request"))
        except MediaUnavailable as e:
            log.info("video desligado: %s", e)
            raise HTTPException(503, detail=_err("Este recurso não está disponível no momento.", "service_unavailable", "video_disabled"))
        REQS.labels(c["name"], "image", "accepted").inc()
        if kind == "video" or body.get("async") or request.headers.get("prefer", "").lower() == "respond-async":
            return JSONResponse({"id": job["id"], "status": job["state"], "poll": f"/v1/jobs/{job['id']}"}, status_code=202)
        job = await m.wait(job["id"], q.s.media_sync_wait_s) or job
        if job["state"] == "done":
            img = await m.image(job["id"])
            fmt = body.get("response_format", "b64_json")
            item = {"b64_json": m.b64(img)} if fmt == "b64_json" else {"url": f"/v1/jobs/{job['id']}/image"}
            return {"created": int(time.time()), "id": job["id"], "seconds": job["seconds"], "size": job["size"], "data": [item]}
        if job["state"] == "failed":
            unavailable = (job.get("error") or "").startswith(("maquina", "nao consegui abrir", "ComfyUI inacessivel"))
            log.warning("falha de midia: %s", job.get("error"))
            raise HTTPException(503 if unavailable else 502, detail=_err(notice.MENSAGEM_FALHA, "service_unavailable" if unavailable else "server_error", "media_failed"))
        return JSONResponse({"id": job["id"], "status": job["state"], "poll": f"/v1/jobs/{job['id']}"}, status_code=202)

    @app.get("/v1/jobs/{job_id}")
    async def job_get(job_id: str, request: Request):
        q: App = request.app.state.q
        c = q.auth(request)
        job = await _media(q).get(job_id)
        if not job or job.get("consumer") != c["name"]:
            raise HTTPException(404, detail=_err("pedido nao encontrado", "invalid_request_error", "not_found"))
        return {k: v for k, v in job.items() if k != "consumer"}

    @app.get("/v1/media/jobs/{job_id}")
    async def media_job_progress(job_id: str, request: Request):
        """Contrato de progresso do app: {id, kind, status: queued|running|done|failed, position, eta_s, progress 0..100, message[, result, mime, seconds, size]}."""
        q: App = request.app.state.q
        c = q.auth(request)
        m = _media(q)
        job = await m.get(job_id)
        if not job or job.get("consumer") != c["name"]:
            raise HTTPException(404, detail=_err("Pedido não encontrado.", "invalid_request_error", "not_found"))
        return await m.progress(job_id)

    @app.get("/v1/jobs/{job_id}/image")
    async def job_image(job_id: str, request: Request, i: int = 0):
        q: App = request.app.state.q
        c = q.auth(request)
        m = _media(q)
        job = await m.get(job_id)
        i = max(0, min(i, 8))
        img = await m.image(job_id, i) if job and job.get("consumer") == c["name"] else None
        if not img:
            raise HTTPException(404, detail=_err("imagem nao encontrada (ou expirou, 48 h)", "invalid_request_error", "not_found"))
        res = (job or {}).get("results") or []
        mime = (res[i]["mime"] if 0 <= i < len(res) else None) or (job or {}).get("mime") or "image/png"
        return Response(content=img, media_type=mime, headers={"Content-Disposition": f'inline; filename="{job_id}.{EXT.get(mime, "bin")}"'})

    @app.get("/v1/jobs/{job_id}/file")
    async def job_file(job_id: str, request: Request):
        return await job_image(job_id, request, int(request.query_params.get("i") or 0))

    @app.get("/admin/media/status")
    async def admin_media_status(request: Request):
        q = _admin(request)
        if q.media is None:
            return {"enabled": False}
        return await q.media.status()

    @app.get("/admin/media/jobs")
    async def admin_media_jobs(request: Request, limit: int = 40):
        q = _admin(request)
        if q.media is None:
            return {"enabled": False, "waiting": [], "running": [], "recent": [], "lane": None}
        return {"enabled": True, **await q.media.jobs(max(1, min(limit, 100)))}

    @app.get("/admin/media/image/{job_id}")
    async def admin_media_image(job_id: str, request: Request):
        """Miniatura/imagem de um pedido para o painel (so admin). O resto da API so entrega a imagem a quem pediu."""
        q = _admin(request)
        img = await q.media.image(job_id) if q.media else None
        if not img:
            raise HTTPException(404, detail=_err("imagem nao encontrada (ou expirou, 48 h)", "invalid_request_error", "not_found"))
        return Response(content=img, media_type=((await q.media.get(job_id)) or {}).get("mime") or "image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/admin/metrics")
    async def admin_metrics(request: Request, machine: str = "llm", hours: int = 24, days: int = 14):
        """Series de uso e latencia de uma maquina (`llm` ou `image`) para os graficos do painel. So metadados."""
        q = _admin(request)
        return await mx.summary(q.r, machine if machine in ("llm", "image") else "llm", max(1, min(hours, 168)), max(1, min(days, 60)))

    @app.get("/admin/billing")
    async def admin_billing(request: Request):
        """Saldo da Vast, preco real de cada maquina, custo programado por mes e dias de cobertura (para o admin saber quando recarregar)."""
        q = _admin(request)
        vast = q.ctl.vast
        try:
            credit = await vast.credit()
            insts = await vast.list_instances()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, detail=_err(f"Vast indisponivel ({type(e).__name__})", "service_unavailable", "vast_unavailable"))
        view = mx.billing_view(credit, insts, q.s.static_instance_id or None, q.s.media_instance_id or None)
        view["spend"] = (await q.ctl.ledger.snapshot()) if hasattr(q.ctl, "ledger") else None
        return view

    @app.get("/admin/gpu/cycles")
    async def admin_cycles(request: Request, limit: int = 50):
        """Log de ligar/desligar da GPU (tela Maquinas do painel): mais recente primeiro; o ciclo em curso vem com stopped_at nulo."""
        q = _admin(request)
        return {"cycles": await q.ctl.cycles.list(max(1, min(limit, 300)))}

    def _admin(request: Request) -> App:
        q: App = request.app.state.q
        key = request.headers.get("x-admin-key", "")
        if not q.s.admin_key_sha256 or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), q.s.admin_key_sha256):
            raise HTTPException(403, detail=_err("chave de admin invalida", "invalid_request_error", "forbidden"))
        return q

    @app.get("/admin/gpu/config")
    async def admin_config_get(request: Request):
        """Config em tempo de execucao (auto_shutdown, idle_min, reserved). A fonte da verdade e o Redis; o env so da o valor inicial."""
        return _admin(request).ctl.config_view()

    @app.post("/admin/gpu/config")
    async def admin_config_set(request: Request):
        q = _admin(request)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail=_err("JSON invalido", "invalid_request_error", "invalid_json"))
        try:
            return await q.ctl.set_config(body, who="admin")
        except ValueError as e:
            raise HTTPException(422, detail=_err(str(e), "invalid_request_error", "invalid_config"))

    async def _confirmed(request: Request) -> bool:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return False
        return isinstance(body, dict) and body.get("i_know_this_loses_the_host") is True

    def _static_locked() -> HTTPException:
        return HTTPException(409, detail=_err("GPU fixa: parar ou destruir perde a maquina. Para forcar, envie {\"i_know_this_loses_the_host\": true}",
                                              "invalid_request_error", "static_instance_protected"))

    @app.post("/admin/gpu/stop")
    async def admin_stop(request: Request):
        """Parada de emergencia: para a instancia (guarda o disco). `docker stop llm-queue` NAO desliga a GPU. Em modo fixo exige confirmacao."""
        q = _admin(request)
        if q.ctl.static and not await _confirmed(request):
            raise _static_locked()
        if q.ctl.state != READY:
            return {"ok": False, "state": q.ctl.state}
        await (q.ctl.stop_now("admin", force=True) if q.ctl.static else q.ctl.stop_now("admin"))
        return {"ok": True, "state": q.ctl.state}

    @app.post("/admin/gpu/destroy")
    async def admin_destroy(request: Request):
        """Destroi a instancia atual (para de pagar disco). A proxima requisicao cria outra do zero. Em modo fixo exige confirmacao."""
        q = _admin(request)
        if q.ctl.static and not await _confirmed(request):
            raise _static_locked()
        await (q.ctl.destroy_now("admin", force=True) if q.ctl.static else q.ctl.destroy_now("admin"))
        return {"ok": True, "state": q.ctl.state}

    @app.get("/metrics")
    async def metrics(request: Request):
        q: App = request.app.state.q
        for st in ("OFF", "STARTING", "FALLBACK", "LOADING", "READY", "STOPPING"):
            GPU_STATE.labels(st).set(1 if q.ctl.state == st else 0)
        for name, lane in (("chat", q.chat), ("embed", q.embed)):
            QUEUE.labels(name).set(len(lane.waiting))
            INFL.labels(name).set(lane.inflight)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        return await _handle(request, "chat", "/chat/completions")

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        """Ditado: repassa o audio (multipart) ao Whisper da maquina do LLM. Nao acorda a GPU nem entra na fila do chat:
        se a maquina nao esta pronta, responde 503 na hora e o app mostra uma frase simples."""
        q: App = request.app.state.q
        q.auth(request)
        if not q.s.stt_enabled:
            raise HTTPException(503, detail=_err("transcricao desligada nesta instalacao (STT_ENABLED)", "service_unavailable", "stt_disabled"))
        if q.ctl.state != READY:
            raise HTTPException(503, detail=_err("a maquina da IA nao esta pronta agora", "service_unavailable", "gpu_unavailable"), headers={"Retry-After": "30"})
        limite = q.s.stt_max_mb * 1024 * 1024
        if int(request.headers.get("content-length") or 0) > limite:
            raise HTTPException(413, detail=_err(f"audio acima de {q.s.stt_max_mb} MB", "invalid_request_error", "audio_too_large"))
        corpo = await request.body()
        if len(corpo) > limite:
            raise HTTPException(413, detail=_err(f"audio acima de {q.s.stt_max_mb} MB", "invalid_request_error", "audio_too_large"))
        sem = getattr(q, "stt_sem", None)
        if sem is None:
            sem = q.stt_sem = asyncio.Semaphore(q.s.stt_concurrency)
        try:
            async with sem:
                r = await q.http.post(q.s.stt_upstream.rstrip("/") + "/v1/audio/transcriptions", content=corpo,
                                      headers={"content-type": request.headers.get("content-type", "application/octet-stream")}, timeout=120.0)
        except httpx.HTTPError:
            raise HTTPException(502, detail=_err("falha ao falar com o servico de transcricao", "server_error", "upstream_error"))
        return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        if not request.app.state.q.s.embed_enabled:
            raise HTTPException(503, detail=_err("embeddings desligado nesta instalacao (GPU_EMBED_MODE=off)", "service_unavailable", "embeddings_disabled"))
        return await _handle(request, "embed", "/embeddings")

    async def _handle(request: Request, kind: str, path: str):
        q: App = request.app.state.q
        consumer = q.auth(request)
        if q.ctl.cfg["reserved"]:  # o Lucas parou o LLM para usar a GPU: nada de acordar nem religar
            raise HTTPException(503, detail=_err("GPU reservada para outra carga; tente de novo mais tarde", "service_unavailable", "gpu_reserved"),
                                headers={"Retry-After": "300"})
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(400, detail=_err("JSON invalido", "invalid_request_error"))
        cls = request.headers.get("x-queue-class", "interactive").lower()
        if cls == "batch" and not q.s.batch_enabled:
            raise HTTPException(403, detail=_err("classe batch desligada (GPU_BATCH_ENABLED=false)", "invalid_request_error", "batch_disabled"))
        if cls not in ("interactive", "batch"):
            raise HTTPException(400, detail=_err("X-Queue-Class deve ser interactive ou batch", "invalid_request_error"))
        if kind == "chat":
            if q.s.repetition_penalty and "repetition_penalty" not in body:
                body["repetition_penalty"] = q.s.repetition_penalty
            approx = approx_chat_tokens(body.get("messages", [])) + int(body.get("max_tokens") or 0)
            if approx > q.s.max_context_tokens * 2:  # folga: a conta de caracteres por 3 superestima
                raise HTTPException(400, detail=_err(f"contexto acima do limite de {q.s.max_context_tokens} tokens", "invalid_request_error", "context_length_exceeded"))
        if request.headers.get("x-queue-wait", "").strip() == "0" and q.ctl.state != READY:
            # cliente que nao quer ficar pendurado: acorda a GPU e responde 202 com o tempo estimado
            q.ctl.request_wake(bool(consumer.get("priority")))
            st = q.ctl.status()
            eta = int(st.get("eta_s") or 120)
            return JSONResponse(status_code=202, headers={"Retry-After": str(eta)}, content={
                "status": "waking", "state": st["state"], "eta_s": st.get("eta_s"),
                "message": "O computador da IA estava desligado e está sendo ligado agora. Tente de novo no tempo indicado em Retry-After."})
        lane = q.lane_for(kind)
        try:
            ticket = lane.admit(consumer["name"], cls, time.time())
        except QueueFull as e:
            REQS.labels(consumer["name"], kind, "429").inc()
            raise HTTPException(429, detail=_err("fila cheia", "rate_limit_error", "queue_full"), headers={"Retry-After": str(e.retry_after)})
        ticket.end_user, ticket.kind = request.headers.get("x-end-user", "")[:60], kind
        stream = bool(body.get("stream")) and kind == "chat"
        media = "text/event-stream" if stream else "application/json"
        headers = {"X-LLM-State": q.ctl.state, "X-LLM-Queue-Position": str(lane.position(ticket)), "Cache-Control": "no-store",
                   "X-Accel-Buffering": "no"}
        fields = ("content",) if request.headers.get("x-queue-notice", "").strip().lower() == "content" else q.s.notice_fields
        gen = _run(q, lane, ticket, consumer, kind, path, body, stream, bool(consumer.get("priority")), fields)
        return StreamingResponse(gen, media_type=media, headers=headers)

    async def _run(q: App, lane: Lane, ticket, consumer: dict, kind: str, path: str, body: dict, stream: bool, priority: bool,
                   fields: tuple = ("reasoning", "reasoning_content")):
        s, name = q.s, consumer["name"]
        keep = b": acordando\n\n" if stream else b" "
        code = "200"
        q.ctl.begin_request()
        got_slot = False
        try:
            # GPU fora do ar e cliente em stream: o aviso sai na hora (nao e erro, e espera), depois a cada notice_every_s
            warn = stream and s.notice_enabled and q.ctl.state != READY
            cid, t_wait, next_warn, warned = notice.new_id(), time.time(), 0.0, False
            prep = asyncio.create_task(_prepare(q, lane, ticket, priority))
            while not prep.done():
                now = time.time()
                if warn and q.ctl.state != READY and now >= next_warn:
                    st = q.ctl.status()
                    txt = notice.first_text(st["path"], q.ctl.static) if not warned else notice.update_text(st["state"], st.get("eta_s"), q.ctl.static)
                    yield notice.chunk(txt, notice.x_queue(st["state"], st.get("eta_s"), now - t_wait, txt, st["path"]), fields, cid)
                    warned, next_warn = True, now + s.notice_every_s
                    continue_wait = min(s.keepalive_s, max(0.05, next_warn - now))
                else:
                    continue_wait = s.keepalive_s if not warn else min(s.keepalive_s, max(0.05, next_warn - now))
                done, _ = await asyncio.wait({prep}, timeout=continue_wait)
                if not done and not (warn and time.time() >= next_warn):
                    yield keep
            try:
                prep.result()
                got_slot = True
                if warned:  # so avisa "pronto" quando a GPU de fato ficou pronta (falha segue para o erro abaixo)
                    st = q.ctl.status()
                    txt = notice.ready_text()
                    yield notice.chunk(txt, notice.x_queue(st["state"], st.get("eta_s"), time.time() - t_wait, txt, st["path"]), fields, cid)
            except GPUUnavailable as e:
                code = "503"
                log.warning("GPU indisponivel (%s), retry em %ss", e.reason, e.retry_after)
                yield _error_chunk(stream, notice.MENSAGEM_FALHA, "service_unavailable", e.reason)
                return
            WAIT.labels(kind).observe(max(0.0, time.time() - ticket.t_enq))
            attempts = 0
            while True:
                sent = False
                try:
                    async with q.http.stream("POST", q.upstream(kind) + path, json=body) as r:
                        if r.status_code != 200:
                            code = str(r.status_code)
                            data = await r.aread()
                            if stream:  # erro do vLLM antes do stream: repassa o JSON do erro
                                yield b"data: " + data.strip() + b"\n\ndata: [DONE]\n\n"
                            else:
                                yield data
                            return
                        if stream:
                            it = r.aiter_raw().__aiter__()
                            while True:
                                try:  # stream parado no meio (vLLM travado) nao pode prender a vaga nem o cliente por 30 min
                                    chunk = await asyncio.wait_for(it.__anext__(), s.stream_idle_timeout_s)
                                except StopAsyncIteration:
                                    break
                                except asyncio.TimeoutError:
                                    code = "504"
                                    log.warning("upstream parou de enviar dados por %s s", int(s.stream_idle_timeout_s))
                                    yield _error_chunk(stream, notice.MENSAGEM_FALHA, "server_error", "upstream_stalled")
                                    return
                                sent = True
                                q.ctl.touch()
                                yield chunk
                        else:
                            data = await r.aread()
                            sent = True
                            yield data
                    return
                except (httpx.TransportError, httpx.HTTPError) as e:
                    if sent or attempts >= 1:  # ja respondeu: nao da para refazer sem duplicar tokens
                        code = "502"
                        log.warning("falha no upstream: %r", e)
                        yield _error_chunk(stream, notice.MENSAGEM_FALHA, "server_error", "upstream_error")
                        return
                    attempts += 1  # nenhum byte enviado: volta para a fila e refaz uma vez
                    try:
                        await q.ctl.ensure_ready(priority)
                    except GPUUnavailable as g:
                        code = "503"
                        log.warning("GPU indisponivel (%s)", g.reason)
                        yield _error_chunk(stream, notice.MENSAGEM_FALHA, "service_unavailable", g.reason)
                        return
        except asyncio.CancelledError:
            code = "499"
            raise
        finally:
            REQS.labels(name, kind, code).inc()
            q.recent.appendleft({"id": ticket.seq, "kind": kind, "consumer": name, "end_user": ticket.end_user, "cls": ticket.cls, "enq": ticket.t_enq,
                                 "start": ticket.t_start, "end": time.time(), "code": code})
            # o que e sincrono vem antes de qualquer await: se o cliente cancelar, o cancelamento pode interromper o primeiro
            # await e a vaga ficaria vazada (aparece "executando" na fila sem nenhum pedido real)
            if got_slot:
                lane.release_now(ticket)
            else:
                lane.cancel_now(ticket)
            q.ctl.end_request()
            try:
                await mx.log_job(q.r, "llm", kind, name, ticket.end_user, ticket.t_enq, ticket.t_start, time.time(), code, ticket.cls)
            except asyncio.CancelledError:
                pass  # o registro e opcional; o pedido ja foi encerrado acima

    async def _prepare(q: App, lane: Lane, ticket, priority: bool) -> None:
        await q.ctl.ensure_ready(priority)
        await lane.acquire(ticket, time.time())

    def _error_chunk(stream: bool, msg: str, typ: str, code: str) -> bytes:
        payload = json.dumps(_err(msg, typ, code), ensure_ascii=False).encode()
        return (b"data: " + payload + b"\n\ndata: [DONE]\n\n") if stream else payload

    return app


def _write_key(b64: str) -> str:
    import base64
    import os
    import tempfile
    d = tempfile.mkdtemp(prefix="llmq-")
    p = os.path.join(d, "key")
    with open(p, "wb") as f:
        f.write(base64.b64decode(b64))
    os.chmod(p, 0o600)
    return p


def _forwards(s: Settings) -> list[tuple[int, int]]:
    def port(u: str) -> int:
        return int(u.split("//")[1].split("/")[0].split(":")[1])
    fw = [(port(s.chat_upstream), 18000)]
    if s.embed_enabled:
        fw.append((port(s.embed_upstream), 18001))
    if s.stt_enabled:
        fw.append((port(s.stt_upstream), 18002))
    return fw


def _make_probe(s: Settings, client: httpx.AsyncClient):
    def base(u: str) -> str:
        return u.rstrip("/").rsplit("/v1", 1)[0]

    async def probe() -> bool:
        try:
            r = await client.get(base(s.chat_upstream) + "/health", timeout=5.0)
            if r.status_code != 200:
                return False
            if s.embed_enabled:
                r2 = await client.get(base(s.embed_upstream) + "/health", timeout=5.0)
                return r2.status_code == 200
            return True
        except Exception:  # noqa: BLE001
            return False
    return probe


app = None  # criado por uvicorn --factory app.main:create_app
