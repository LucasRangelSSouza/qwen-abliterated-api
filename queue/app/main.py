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
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app import notice
from app.alerts import Alerts
from app.config import Settings
from app.gpu.controller import READY, GPUController, GPUUnavailable
from app.gpu.ledger import Ledger
from app.gpu.tunnel import NoTunnel, SshTunnel
from app.gpu.vast import RealVast
from app.scheduler import Lane, QueueFull

log = logging.getLogger("llm-queue")

REQS = Counter("llmq_requests_total", "requisicoes", ["consumer", "kind", "code"])
WAIT = Histogram("llmq_wait_seconds", "espera ate o inicio do servico", ["kind"], buckets=(1, 5, 15, 60, 180, 360, 720, 900))
GPU_STATE = Gauge("llmq_gpu_state", "estado da GPU (1 = atual)", ["state"])
QUEUE = Gauge("llmq_queue_waiting", "itens esperando", ["kind"])
INFL = Gauge("llmq_inflight", "em voo", ["kind"])


def _err(message: str, typ: str = "server_error", code: str | None = None) -> dict:
    return {"error": {"message": message, "type": typ, "code": code}}


class App:
    """Agrupa as pecas (usado pela fabrica e pelos testes, que injetam fakes)."""
    def __init__(self, s: Settings, controller: GPUController, redis, http: httpx.AsyncClient, alerts: Alerts):
        self.s, self.ctl, self.r, self.http, self.alerts = s, controller, redis, http, alerts
        self.chat = Lane("chat", s.chat_max_inflight, s.chat_queue_max, s.chat_per_consumer)
        self.embed = Lane("embed", s.embed_max_inflight, s.embed_queue_max, s.embed_max_inflight)
        self.consumers = self._load_consumers()
        self.recent: collections.deque = collections.deque(maxlen=60)  # ultimos pedidos encerrados (so metadados)

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
               consumers: dict | None = None) -> FastAPI:
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
        if consumers is not None:
            app.state.q.consumers = consumers
        try:
            yield
        finally:
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
        st = q.ctl.status()
        led = await q.ctl.ledger.snapshot()
        st.update({"limits": {"idle_min": q.s.idle_min, "max_daily_usd": q.s.max_daily_usd, "monthly_budget_usd": q.s.monthly_budget_usd,
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

    @app.post("/admin/gpu/stop")
    async def admin_stop(request: Request):
        """Parada de emergencia: para a instancia (guarda o disco). `docker stop llm-queue` NAO desliga a GPU."""
        q = _admin(request)
        if q.ctl.state != READY:
            return {"ok": False, "state": q.ctl.state}
        await q.ctl.stop_now("admin")
        return {"ok": True, "state": q.ctl.state}

    @app.post("/admin/gpu/destroy")
    async def admin_destroy(request: Request):
        """Destroi a instancia atual (para de pagar disco). A proxima requisicao cria outra do zero."""
        q = _admin(request)
        await q.ctl.destroy_now("admin")
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

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        if not request.app.state.q.s.embed_enabled:
            raise HTTPException(503, detail=_err("embeddings desligado nesta instalacao (GPU_EMBED_MODE=off)", "service_unavailable", "embeddings_disabled"))
        return await _handle(request, "embed", "/embeddings")

    async def _handle(request: Request, kind: str, path: str):
        q: App = request.app.state.q
        consumer = q.auth(request)
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
            approx = len(json.dumps(body.get("messages", []), ensure_ascii=False)) // 3 + int(body.get("max_tokens") or 0)
            if approx > q.s.max_context_tokens * 2:  # folga: a conta de caracteres por 3 superestima
                raise HTTPException(400, detail=_err("contexto acima do limite de 65536 tokens", "invalid_request_error", "context_length_exceeded"))
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
                    txt = notice.first_text(st["path"]) if not warned else notice.update_text(st["state"], st.get("eta_s"))
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
                yield _error_chunk(stream, f"GPU indisponivel ({e.reason}): tente novamente em {e.retry_after}s", "service_unavailable", e.reason)
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
                            async for chunk in r.aiter_raw():
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
                        yield _error_chunk(stream, f"falha no upstream: {e!r}", "server_error", "upstream_error")
                        return
                    attempts += 1  # nenhum byte enviado: volta para a fila e refaz uma vez
                    try:
                        await q.ctl.ensure_ready(priority)
                    except GPUUnavailable as g:
                        code = "503"
                        yield _error_chunk(stream, f"GPU indisponivel ({g.reason})", "service_unavailable", g.reason)
                        return
        except asyncio.CancelledError:
            code = "499"
            raise
        finally:
            REQS.labels(name, kind, code).inc()
            q.recent.appendleft({"id": ticket.seq, "kind": kind, "consumer": name, "end_user": ticket.end_user, "cls": ticket.cls, "enq": ticket.t_enq,
                                 "start": ticket.t_start, "end": time.time(), "code": code})
            if got_slot:
                await lane.release(ticket)
            else:
                await lane.cancel(ticket)
            q.ctl.end_request()

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
