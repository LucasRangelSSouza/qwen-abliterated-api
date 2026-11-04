"""Fila de midia: imagens geradas pelo ComfyUI de uma GPU fixa (3090 alugada na Vast), alcancada por tunel SSH.
Mesma ideia do chat: os clientes so enxergam o llm-queue. Uma imagem por vez (a GPU e uma so), fila FIFO com `interactive` antes de `batch`,
historico dos pedidos em Redis por 48 h (quem pediu, o que pediu, quanto levou) e a imagem pronta guardada no proprio Redis, para
leitura nao depender de a GPU estar de pe. Receitas (workflows) ficam fixas aqui: o LLM escolhe a receita e preenche parametros,
nunca monta o grafo."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
import uuid

import httpx

from app import metrics
from app.config import Settings
from app.gpu.tunnel import Tunnel
from app.scheduler import Lane, QueueFull

log = logging.getLogger("llm-queue.media")

JOB_TTL_S = 48 * 3600
RECENT_MAX = 100
MAX_PROMPT_CHARS = 4000


class MediaUnavailable(Exception):
    """A maquina de imagem esta fora do ar ou ainda subindo."""


class MediaError(Exception):
    """O ComfyUI recusou ou falhou ao gerar."""


# --------------------------------------------------------------------------- receitas

def _size(p: dict) -> tuple[int, int]:
    """Aceita "1024x1024" (ou width/height). Arredonda para multiplos de 64 e limita a 1,6 MP para caber na 3090 com folga."""
    w = h = 1024
    sz = str(p.get("size") or "")
    if "x" in sz:
        try:
            a, b = sz.lower().split("x", 1)
            w, h = int(a), int(b)
        except ValueError:
            pass
    w = int(p.get("width") or w)
    h = int(p.get("height") or h)
    w, h = max(256, min(1536, round(w / 64) * 64)), max(256, min(1536, round(h / 64) * 64))
    while w * h > 1_650_000:
        w, h = max(256, w - 64), max(256, h - 64)
    return w, h


def recipe_flux_schnell(p: dict, ckpt: str) -> dict:
    w, h = _size(p)
    seed = int(p["seed"]) if p.get("seed") is not None else random.randint(0, 2**53)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": str(p["prompt"]), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
                                                    "seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "midia"}},
    }


VIDEO_SIZES = {"paisagem": (768, 512), "retrato": (512, 768), "quadrado": (576, 576)}
NEG_VIDEO = "worst quality, inconsistent motion, blurry, jittery, distorted, watermark, text, deformed"


def _video_size(p: dict) -> tuple[int, int]:
    w, h = VIDEO_SIZES.get(str(p.get("format") or p.get("formato") or "paisagem"), VIDEO_SIZES["paisagem"])
    sz = str(p.get("size") or "")
    if "x" in sz:
        try:
            a, b = sz.lower().split("x", 1)
            w, h = max(256, min(832, round(int(a) / 32) * 32)), max(256, min(832, round(int(b) / 32) * 32))
        except ValueError:
            pass
    return w, h


def recipe_ltx_video(p: dict, _ckpt: str) -> dict:
    """Texto para video (LTX-Video 2B): clipes curtos de 2 a 6 s a 25 quadros por segundo."""
    w, h = _video_size(p)
    secs = max(1.0, min(6.0, float(p.get("seconds") or p.get("segundos") or 4)))
    frames = int(round(secs * 25 / 8)) * 8 + 1  # o modelo pede 8n+1 quadros
    seed = int(p["seed"]) if p.get("seed") is not None else random.randint(0, 2**53)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": str(p["prompt"]), "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_VIDEO, "clip": ["2", 0]}},
        "5": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": 25.0}},
        "6": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": w, "height": h, "length": frames, "batch_size": 1}},
        "7": {"class_type": "LTXVScheduler", "inputs": {"steps": 30, "max_shift": 2.05, "base_shift": 0.95, "stretch": True, "terminal": 0.1, "latent": ["6", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "SamplerCustom", "inputs": {"model": ["1", 0], "add_noise": True, "noise_seed": seed, "cfg": 3.0, "positive": ["5", 0], "negative": ["5", 1],
                                                       "sampler": ["8", 0], "sigmas": ["7", 0], "latent_image": ["6", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 25.0}},
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0], "filename_prefix": "video/midia", "format": "mp4", "codec": "h264"}},
    }


RECIPES = {
    "flux-schnell": {"kind": "image", "title": "Flux schnell (rapido, boa qualidade)", "build": recipe_flux_schnell, "default": True},
    "ltx-video": {"kind": "video", "title": "LTX-Video 2B (texto para video, 2 a 6 s)", "build": recipe_ltx_video, "default_video": True, "timeout": 900},
}


# --------------------------------------------------------------------------- servico

class Media:
    def __init__(self, s: Settings, vast, tunnel: Tunnel, redis, http: httpx.AsyncClient, clock=time.time):
        self.s, self.vast, self.tun, self.r, self.http, self.now = s, vast, tunnel, redis, http, clock
        self.lane = Lane("image", 1, s.media_queue_max, s.media_per_consumer)
        self.live: dict[str, dict] = {}        # pedidos que ainda nao terminaram (fila + rodando)
        self.tasks: dict[str, asyncio.Task] = {}
        self._inst_cache: tuple[float, object] | None = None
        self._tun_lock = asyncio.Lock()
        self.comfy = f"http://127.0.0.1:{s.media_local_port}"
        self._keep: asyncio.Task | None = None

    # ---- vigia do tunel
    def start(self) -> None:
        if self._keep is None:
            self._keep = asyncio.create_task(self._keeper())

    async def stop(self) -> None:
        k, self._keep = self._keep, None
        if k:
            k.cancel()
            await asyncio.gather(k, return_exceptions=True)
        await self.tun.close()

    async def _keeper(self) -> None:
        """Mantem o tunel ate o ComfyUI aberto enquanto a maquina de imagem estiver de pe (24/7), para o painel mostrar a verdade e o 1o pedido nao pagar a abertura."""
        while True:
            try:
                if not await self.tun.healthy():
                    inst = await self.instance(fresh=True)
                    if inst is not None and inst.running:
                        await self._ensure_tunnel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("media: vigia do tunel: %r", e)
            await asyncio.sleep(self.s.media_keepalive_s)

    # ---- maquina
    async def instance(self, fresh: bool = False):
        if not fresh and self._inst_cache and self.now() - self._inst_cache[0] < 8:
            return self._inst_cache[1]
        try:
            inst = await self.vast.get_instance(self.s.media_instance_id)
        except Exception as e:  # noqa: BLE001
            log.warning("media: nao consegui consultar a Vast: %r", e)
            return self._inst_cache[1] if self._inst_cache else None
        self._inst_cache = (self.now(), inst)
        return inst

    async def _ensure_tunnel(self) -> None:
        async with self._tun_lock:
            if await self.tun.healthy():
                return
            inst = await self.instance(fresh=True)
            if inst is None:
                raise MediaUnavailable("maquina de imagem nao encontrada na Vast")
            if not inst.running:
                raise MediaUnavailable(f"maquina de imagem indisponivel ({inst.actual_status or 'sem estado'})")
            if not await self.tun.open(inst.ssh_host, int(inst.ssh_port)):
                raise MediaUnavailable("nao consegui abrir o tunel ate a maquina de imagem")

    async def comfy_health(self) -> dict | None:
        try:
            r = await self.http.get(self.comfy + "/system_stats", timeout=5)
            r.raise_for_status()
            j = r.json()
            dev = (j.get("devices") or [{}])[0]
            return {"version": (j.get("system") or {}).get("comfyui_version"), "gpu": dev.get("name"),
                    "vram_total": dev.get("vram_total"), "vram_free": dev.get("vram_free")}
        except Exception:  # noqa: BLE001
            return None

    async def status(self) -> dict:
        inst = await self.instance()
        tun_ok = await self.tun.healthy()
        comfy = await self.comfy_health() if tun_ok else None
        raw = getattr(inst, "raw", {}) or {}
        today = await self._today_stats()
        state = "off"
        if inst is not None:
            state = "ready" if (inst.running and comfy) else "starting" if inst.actual_status in ("loading", "created", "scheduling") else "degraded" if inst.running else "off"
        return {"enabled": True, "state": state, "tunnel": tun_ok,
                "instance": None if inst is None else {"id": inst.id, "status": inst.actual_status, "dph": round(inst.dph_total, 4),
                                                         "gpu": raw.get("gpu_name"), "num_gpus": raw.get("num_gpus"), "location": raw.get("geolocation"),
                                                         "label": raw.get("label"), "disk_gb": raw.get("disk_space"), "cpu_ram_gb": round((raw.get("cpu_ram") or 0) / 1024)},
                "comfy": comfy, "lane": self.lane.stats(), "recipes": [{"id": k, "kind": v["kind"], "title": v["title"]} for k, v in RECIPES.items()],
                "today": today, "monthly_cost_usd": round((inst.dph_total if inst else 0) * 24 * 30, 2)}

    async def _today_stats(self) -> dict:
        rows = await self._recent_rows(RECENT_MAX)
        cut = self.now() - 86400
        day = [j for j in rows if (j.get("finished") or 0) >= cut]
        ok = [j for j in day if j.get("state") == "done"]
        avg = round(sum(j.get("seconds") or 0 for j in ok) / len(ok), 1) if ok else None
        return {"done": len(ok), "failed": len(day) - len(ok), "avg_seconds": avg}

    # ---- jobs
    async def submit(self, consumer: str, end_user: str, params: dict, cls: str = "interactive", kind: str = "image") -> dict:
        recipe = params.get("recipe") or next(k for k, v in RECIPES.items() if v.get("default" if kind == "image" else "default_video"))
        if recipe not in RECIPES or RECIPES[recipe]["kind"] != kind:
            raise MediaError(f"receita desconhecida: {recipe}")
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise MediaError("o pedido precisa de um prompt")
        params = {**params, "prompt": prompt[:MAX_PROMPT_CHARS]}
        t = self.lane.admit(consumer, cls, self.now())  # QueueFull sobe para o chamador (429)
        t.end_user, t.kind = end_user, kind
        jid = uuid.uuid4().hex[:12]
        w, h = _video_size(params) if kind == "video" else _size(params)
        job = {"id": jid, "state": "na_fila", "recipe": recipe, "kind": kind, "consumer": consumer, "end_user": end_user, "cls": cls,
               "prompt": prompt[:300], "size": f"{w}x{h}", "enq": self.now(), "started": None, "finished": None, "seconds": None, "error": None}
        self.live[jid] = job
        await self._save(job)
        self.tasks[jid] = asyncio.create_task(self._run(job, params, t))
        return job

    async def _run(self, job: dict, params: dict, t) -> None:
        try:
            await self.lane.acquire(t, self.now())
            job.update(state="executando", started=self.now())
            await self._save(job)
            await self._ensure_tunnel()
            wf = RECIPES[job["recipe"]]["build"](params, self.s.media_ckpt)
            img, mime = await self._comfy_run(wf, job, RECIPES[job["recipe"]].get("timeout"))
            await self.r.setex(f"llmq:media:img:{job['id']}", JOB_TTL_S, img)
            job.update(state="done", finished=self.now(), bytes=len(img), mime=mime)
        except (MediaUnavailable, MediaError) as e:
            job.update(state="failed", finished=self.now(), error=str(e))
        except asyncio.CancelledError:
            job.update(state="failed", finished=self.now(), error="cancelado")
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("media: falha inesperada")
            job.update(state="failed", finished=self.now(), error=f"erro inesperado ({type(e).__name__})")
        finally:
            await self.lane.cancel(t)
            if job.get("started"):
                job["seconds"] = round(job["finished"] - job["started"], 1)
            await self._save(job)
            await self.r.lpush("llmq:media:recent", job["id"])
            await metrics.log_job(self.r, "image", job.get("kind", "image"), job["consumer"], job["end_user"], job["enq"], job.get("started"), job["finished"] or self.now(),
                                  "200" if job["state"] == "done" else "500", job.get("cls", "interactive"))
            await self.r.ltrim("llmq:media:recent", 0, RECENT_MAX - 1)
            self.live.pop(job["id"], None)
            self.tasks.pop(job["id"], None)

    async def _comfy_run(self, wf: dict, job: dict, timeout: float | None = None) -> tuple[bytes, str]:
        try:
            r = await self.http.post(self.comfy + "/prompt", json={"prompt": wf, "client_id": "llm-queue"}, timeout=30)
        except httpx.HTTPError as e:
            raise MediaUnavailable(f"ComfyUI inacessivel ({type(e).__name__})")
        if r.status_code != 200:
            raise MediaError("o ComfyUI recusou o pedido: " + r.text[:200])
        pid = r.json()["prompt_id"]
        limit = max(self.s.media_job_timeout_s, timeout or 0)
        deadline = self.now() + limit
        while self.now() < deadline:
            await asyncio.sleep(self.s.media_poll_s)
            try:
                h = await self.http.get(f"{self.comfy}/history/{pid}", timeout=15)
            except httpx.HTTPError as e:
                raise MediaUnavailable(f"ComfyUI inacessivel ({type(e).__name__})")
            entry = (h.json() or {}).get(pid) if h.status_code == 200 else None
            if not entry:
                continue
            st = (entry.get("status") or {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                detail = (msgs[0][1].get("exception_message") if msgs else "") or "erro de execucao"
                raise MediaError("falha ao gerar: " + str(detail)[:200])
            for out in (entry.get("outputs") or {}).values():
                for im in out.get("images", []):
                    v = await self.http.get(f"{self.comfy}/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")}, timeout=60)
                    if v.status_code == 200 and v.content:
                        return v.content, (v.headers.get("content-type") or "image/png").split(";")[0]
            if st.get("completed"):
                raise MediaError("o ComfyUI terminou sem devolver imagem")
        raise MediaError(f"tempo esgotado ({int(limit)} s)")

    # ---- persistencia / consultas
    async def _save(self, job: dict) -> None:
        await self.r.setex(f"llmq:media:job:{job['id']}", JOB_TTL_S, json.dumps(job))

    async def get(self, jid: str) -> dict | None:
        if jid in self.live:
            return dict(self.live[jid])
        raw = await self.r.get(f"llmq:media:job:{jid}")
        return json.loads(raw) if raw else None

    async def image(self, jid: str) -> bytes | None:
        return await self.r.get(f"llmq:media:img:{jid}")

    async def wait(self, jid: str, timeout: float) -> dict | None:
        task = self.tasks.get(jid)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        return await self.get(jid)

    async def _recent_rows(self, n: int) -> list[dict]:
        ids = [i.decode() if isinstance(i, bytes) else i for i in await self.r.lrange("llmq:media:recent", 0, n - 1)]
        rows = []
        for i in ids:
            raw = await self.r.get(f"llmq:media:job:{i}")
            if raw:
                rows.append(json.loads(raw))
        return rows

    async def jobs(self, limit: int = 40) -> dict:
        now = self.now()
        live = sorted(self.live.values(), key=lambda j: j["enq"])
        shape = lambda j: {k: j.get(k) for k in ("id", "state", "recipe", "kind", "mime", "consumer", "end_user", "cls", "prompt", "size", "enq", "started", "finished", "seconds", "error")}
        waiting = [{**shape(j), "waited_s": round(now - j["enq"], 1)} for j in live if j["state"] == "na_fila"]
        running = [{**shape(j), "running_s": round(now - j["started"], 1) if j.get("started") else None} for j in live if j["state"] == "executando"]
        recent = [shape(j) for j in await self._recent_rows(limit)]
        return {"lane": self.lane.stats(), "waiting": waiting, "running": running, "recent": recent}

    def b64(self, img: bytes) -> str:
        return base64.b64encode(img).decode()


__all__ = ["Media", "MediaUnavailable", "MediaError", "QueueFull", "RECIPES"]
