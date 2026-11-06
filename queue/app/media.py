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

from app import longvideo, metrics, notice
from app import mediaparams as mp
from app.config import Settings
from app.gpu.tunnel import Tunnel
from app.scheduler import Lane, QueueFull

log = logging.getLogger("llm-queue.media")

EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm"}
MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "mp4": "video/mp4", "webm": "video/webm"}
JOB_TTL_S = 48 * 3600
RECENT_MAX = 100
MAX_PROMPT_CHARS = 4000


# Estimativa de duracao quando ainda nao ha medida real (3090: imagem ~30 s, video de 4 s ~40 s, ja contando a troca de modelo).
ETA_DEFAULT_S = {"image": 30, "video": 60}
MSG = {
    ("image", "queued"): "Sua imagem está na fila para ser gerada.",
    ("video", "queued"): "Seu vídeo está na fila para ser gerado.",
    ("image", "running"): "Gerando a sua imagem...",
    ("video", "running"): "Gerando o seu vídeo...",
    ("image", "done"): "Pronto.",
    ("video", "done"): "Pronto.",
}


async def _ws_connect(url: str):
    from websockets.asyncio.client import connect  # so a producao precisa; os testes injetam um falso
    return connect(url, open_timeout=5, max_size=2**22)


class MediaUnavailable(Exception):
    """A maquina de imagem esta fora do ar ou ainda subindo."""


class MediaError(Exception):
    """O ComfyUI recusou ou falhou ao gerar."""


# --------------------------------------------------------------------------- receitas

VIDEO_MAX_PIXELS = mp.VIDEO_MAX_PIXELS
VIDEO_MAX_SECONDS = mp.VIDEO_MAX_SECONDS
VIDEO_SIZES = mp.VIDEO_SIZES
NEG_VIDEO = mp.NEG_VIDEO


def _size(p: dict) -> tuple[int, int]:
    e, _ = mp.normalize_image(p if p.get("prompt") else {**p, "prompt": "x"})
    return e["width"], e["height"]


def _video_size(p: dict) -> tuple[int, int]:
    e, _ = mp.normalize_video(p if p.get("prompt") else {**p, "prompt": "x"})
    return e["width"], e["height"]


def recipe_flux_schnell(p: dict, ckpt: str, image_name: str | None = None) -> dict:
    e, _ = mp.normalize_image(p)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": mp.with_style(e["prompt"], e["style"]), "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": e["negative_prompt"], "clip": ["1", 1]}},  # so tem efeito com cfg > 1
        "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": e["width"], "height": e["height"], "batch_size": e["n"]}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
                                                    "seed": e["seed"], "steps": e["steps"], "cfg": e["cfg"], "sampler_name": e["sampler"], "scheduler": e["scheduler"], "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "midia"}},
    }


def recipe_ltx_video(p: dict, _ckpt: str, image_name: str | None = None) -> dict:
    """Texto para video (LTX-Video 2B) ou imagem para video (image_name = arquivo ja enviado ao ComfyUI): clipes de 1 a 4 s."""
    e, _ = mp.normalize_video(p)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": e["prompt"], "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": e["negative_prompt"], "clip": ["2", 0]}},
        "7": {"class_type": "LTXVScheduler", "inputs": {"steps": e["steps"], "max_shift": 2.05, "base_shift": 0.95, "stretch": True, "terminal": 0.1, "latent": ["6", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": e["sampler"]}},
        "9": {"class_type": "SamplerCustom", "inputs": {"model": ["1", 0], "add_noise": True, "noise_seed": e["seed"], "cfg": e["cfg"], "positive": ["5", 0], "negative": ["5", 1],
                                                       "sampler": ["8", 0], "sigmas": ["7", 0], "latent_image": ["6", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": float(e["fps"])}},
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0], "filename_prefix": "video/midia", "format": "mp4", "codec": "h264"}},
    }
    if image_name:  # imagem para video: o primeiro quadro vem da imagem; strength baixo da mais liberdade ao movimento
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        wf["21"] = {"class_type": "LTXVPreprocess", "inputs": {"image": ["20", 0], "img_compression": 35}}
        wf["22"] = {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "vae": ["1", 2], "image": ["21", 0], "width": e["width"], "height": e["height"],
                                                               "length": e["frames"], "batch_size": 1, "strength": e["strength"] if e["strength"] is not None else 1.0}}
        wf["5"] = {"class_type": "LTXVConditioning", "inputs": {"positive": ["22", 0], "negative": ["22", 1], "frame_rate": float(e["fps"])}}
        wf["7"]["inputs"]["latent"] = ["22", 2]
        wf["9"]["inputs"]["latent_image"] = ["22", 2]
    else:
        wf["5"] = {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": float(e["fps"])}}
        wf["6"] = {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": e["width"], "height": e["height"], "length": e["frames"], "batch_size": 1}}
    return wf


RECIPES = {
    "flux-schnell": {"kind": "image", "title": "Flux schnell (rapido, boa qualidade)", "build": recipe_flux_schnell, "default": True},
    "ltx-video": {"kind": "video", "title": "LTX-Video 2B (texto para video; clipes de ate 4 s encadeados ate 60 s)", "build": recipe_ltx_video, "default_video": True, "timeout": 900},
}


# --------------------------------------------------------------------------- servico

class Media:
    def __init__(self, s: Settings, vast, tunnel: Tunnel, redis, http: httpx.AsyncClient, clock=time.time, ws_connect=None):
        self.s, self.vast, self.tun, self.r, self.http, self.now = s, vast, tunnel, redis, http, clock
        self.lane = Lane("image", 1, s.media_queue_max, s.media_per_consumer)
        self.live: dict[str, dict] = {}        # pedidos que ainda nao terminaram (fila + rodando)
        self.tasks: dict[str, asyncio.Task] = {}
        self._inst_cache: tuple[float, object] | None = None
        self._tun_lock = asyncio.Lock()
        self.comfy = f"http://127.0.0.1:{s.media_local_port}"
        self._keep: asyncio.Task | None = None
        self.tickets: dict[str, object] = {}   # vaga de cada pedido vivo (para dizer a posicao na fila)
        self.prog: dict[str, int] = {}         # progresso real 0..100 dos pedidos em execucao
        self.ws_connect = ws_connect or _ws_connect

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
        if kind == "video" and not self.s.media_video_enabled:
            raise MediaUnavailable("geracao de video indisponivel nesta maquina de midia (sem memoria para video e imagem juntos); use a skill video-motion no computador")
        recipe = params.get("recipe") or next(k for k, v in RECIPES.items() if v.get("default" if kind == "image" else "default_video"))
        if recipe not in RECIPES or RECIPES[recipe]["kind"] != kind:
            raise MediaError(f"receita desconhecida: {recipe}")
        try:
            eff, adjusted = (mp.normalize_image if kind == "image" else mp.normalize_video)(params)
        except mp.ParamError as e:
            raise MediaError(str(e))
        params = {**params, **eff}
        t = self.lane.admit(consumer, cls, self.now())  # QueueFull sobe para o chamador (429)
        t.end_user, t.kind = end_user, kind
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "state": "na_fila", "recipe": recipe, "kind": kind, "type": kind, "consumer": consumer, "end_user": end_user, "cls": cls,
               "prompt": eff["prompt"][:300], "size": f"{eff['width']}x{eff['height']}", "params": mp.public(eff), "adjusted": adjusted,
               "enq": self.now(), "started": None, "finished": None, "seconds": None, "error": None}
        self.live[jid] = job
        self.tickets[jid] = t
        await self._save(job)
        self.tasks[jid] = asyncio.create_task(self._run(job, params, t))
        return job

    async def _run(self, job: dict, params: dict, t) -> None:
        try:
            await self.lane.acquire(t, self.now())
            job.update(state="executando", started=self.now())
            await self._save(job)
            await self._ensure_tunnel()
            n_seg = int(params.get("segments") or 1) if job["kind"] == "video" else 1
            if n_seg > 1:
                files = await self._run_chain(job, params, n_seg)
            else:
                image_name = await self._upload_start_image(params) if job["kind"] == "video" and params.get("image_to_video") else None
                wf = RECIPES[job["recipe"]]["build"](params, self.s.media_ckpt, image_name)
                files = await self._comfy_run(wf, job, RECIPES[job["recipe"]].get("timeout"))
            results = []
            for i, (blob, mime) in enumerate(files):
                await self.r.setex(f"llmq:media:img:{job['id']}" + (f":{i}" if i else ""), JOB_TTL_S, blob)
                results.append({"index": i, "type": job["kind"], "mime": mime, "ext": EXT.get(mime, "bin"), "bytes": len(blob), "url": f"/v1/jobs/{job['id']}/file" + (f"?i={i}" if i else "")})
            job.update(state="done", finished=self.now(), bytes=results[0]["bytes"], mime=results[0]["mime"], ext=results[0]["ext"], results=results)
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
                job["seconds"] = round((job["finished"] or self.now()) - job["started"], 1)
            await self._save(job)
            await self.r.lpush("llmq:media:recent", job["id"])
            await metrics.log_job(self.r, "image", job.get("kind", "image"), job["consumer"], job["end_user"], job["enq"], job.get("started"), job["finished"] or self.now(),
                                  "200" if job["state"] == "done" else "500", job.get("cls", "interactive"))
            await self.r.ltrim("llmq:media:recent", 0, RECENT_MAX - 1)
            self.live.pop(job["id"], None)
            self.tasks.pop(job["id"], None)
            self.tickets.pop(job["id"], None)
            self.prog.pop(job["id"], None)

    async def _run_chain(self, job: dict, params: dict, n_seg: int) -> list[tuple[bytes, str]]:
        """Video longo: n clipes encadeados pelo ultimo quadro, juntados num MP4 so (app/longvideo.py)."""
        clips: list[bytes] = []
        job.update(segments=n_seg, seg_done=0)
        base = int(params.get("seed") or 0)
        last = ""
        for k in range(n_seg):
            pk = {**params, "segments": 1, "seed": base + k}
            if k:
                pk.update(image=last, image_to_video=True, strength=params.get("strength") or 1.0)
            image_name = await self._upload_start_image(pk) if pk.get("image_to_video") else None
            wf = RECIPES[job["recipe"]]["build"](pk, self.s.media_ckpt, image_name)
            blob, _mime = (await self._comfy_run(wf, job, RECIPES[job["recipe"]].get("timeout")))[0]
            clips.append(blob)
            job["seg_done"] = k + 1
            if k + 1 < n_seg:
                last = await longvideo.last_frame_b64(blob)
        return [(await longvideo.concat(clips), "video/mp4")]

    async def _comfy_run(self, wf: dict, job: dict, timeout: float | None = None) -> list[tuple[bytes, str]]:
        watcher = asyncio.create_task(self._watch(job["id"]))  # progresso real; se o WebSocket falhar, o pedido segue so com o polling
        try:
            return await self._comfy_run_inner(wf, job, timeout)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def _watch(self, jid: str) -> None:
        """Acompanha o ComfyUI pelo WebSocket (clientId = id do pedido): 'progress' traz passo/total da amostragem. 5% = carregando modelos, 97% = decodificando."""
        try:
            conn = await self.ws_connect(f"ws://127.0.0.1:{self.s.media_local_port}/ws?clientId={jid}")
            async with conn as ws:
                async for raw in ws:
                    if isinstance(raw, (bytes, bytearray)):
                        continue
                    m = json.loads(raw)
                    d = m.get("data") or {}
                    cur = self.prog.get(jid, 0)
                    if m.get("type") == "execution_start":
                        self.prog[jid] = max(cur, 3)
                    elif m.get("type") == "progress" and d.get("max"):
                        self.prog[jid] = max(cur, min(97, 5 + int(92 * d["value"] / d["max"])))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("media: websocket de progresso indisponivel: %r", e)

    async def _comfy_run_inner(self, wf: dict, job: dict, timeout: float | None = None) -> list[tuple[bytes, str]]:
        try:
            r = await self.http.post(self.comfy + "/prompt", json={"prompt": wf, "client_id": job["id"]}, timeout=30)
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
            found = [im for out in (entry.get("outputs") or {}).values() for im in out.get("images", [])]
            if found:
                files = []
                for im in found:
                    ext = im["filename"].rsplit(".", 1)[-1].lower() if "." in im["filename"] else ""
                    want = MIME_BY_EXT.get(ext)
                    if want is None or want.split("/")[0] != job["kind"]:
                        continue  # o ComfyUI tambem lista previas: so entra o arquivo do tipo pedido (video mp4, imagem png)
                    v = await self.http.get(f"{self.comfy}/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")}, timeout=120)
                    if v.status_code == 200 and v.content:
                        files.append((v.content, want))
                if files:
                    return files
            if st.get("completed"):
                raise MediaError("o ComfyUI terminou sem devolver o arquivo do tipo pedido")
        raise MediaError(f"tempo esgotado ({int(limit)} s)")

    # ---- persistencia / consultas
    async def _save(self, job: dict) -> None:
        await self.r.setex(f"llmq:media:job:{job['id']}", JOB_TTL_S, json.dumps(job))

    async def get(self, jid: str) -> dict | None:
        if jid in self.live:
            return dict(self.live[jid])
        raw = await self.r.get(f"llmq:media:job:{jid}")
        return json.loads(raw) if raw else None

    async def progress(self, jid: str) -> dict | None:
        """Contrato de progresso para o app (GET /v1/media/jobs/{id}). Nunca devolve texto de infraestrutura."""
        job = await self.get(jid)
        if job is None:
            return None
        kind = job.get("kind", "image")
        state = job.get("state")
        live = jid in self.live
        if state in ("na_fila", "executando") and not live:  # o servico reiniciou no meio: o pedido nao existe mais
            state = "failed"
        position = eta = None
        pct = 0
        if state == "done":
            status, pct, eta, msg = "done", 100, 0, MSG[(kind, "done")]
        elif state == "failed":
            status, pct, msg = "failed", int(self.prog.get(jid, 0)), notice.MENSAGEM_FALHA
        elif state == "executando":
            status, pct = "running", int(self.prog.get(jid, 0))
            if job.get("segments", 1) > 1:  # video longo: cada clipe vale uma fatia da barra
                pct = int((job.get("seg_done", 0) * 100 + pct) / job["segments"])
            run = max(0.0, self.now() - (job.get("started") or self.now()))
            est = ETA_DEFAULT_S[kind]
            eta = int(round(run * (100 - pct) / pct)) if pct >= 10 and run > 3 else max(1, int(est - run))
            msg = MSG[(kind, "running")]
        else:
            status, msg = "queued", MSG[(kind, "queued")]
            t = self.tickets.get(jid)
            waiting = list(self.lane.waiting)
            position = waiting.index(t) + 1 if t in waiting else 1
            ahead = waiting[:position - 1]
            eta = sum(ETA_DEFAULT_S.get(getattr(x, "kind", "image"), 30) for x in ahead)
            for r in self.lane.running:  # o que esta rodando agora ainda leva o que falta dele
                rj = self.live.get(next((i for i, tk in self.tickets.items() if tk is r), ""), {})
                rest = ETA_DEFAULT_S[rj.get("kind", "image")] * (100 - self.prog.get(rj.get("id", ""), 0)) / 100
                eta += int(rest)
            eta += ETA_DEFAULT_S[kind]
        out = {"id": jid, "kind": kind, "type": kind, "status": status, "position": position, "eta_s": eta, "progress": pct, "message": msg,
               "params": job.get("params"), "adjusted": job.get("adjusted") or []}
        if status == "done":
            out.update(result=f"/v1/jobs/{jid}/file", mime=job.get("mime"), ext=job.get("ext"), results=job.get("results") or [], seconds=job.get("seconds"), size=job.get("size"))
        return out

    async def _upload_start_image(self, params: dict) -> str:
        raw = str(params.get("image") or params.get("start_image") or "")
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        blob = base64.b64decode(raw)
        ext, mime = ("png", "image/png") if blob.startswith(b"\x89PNG") else ("webp", "image/webp") if blob.startswith(b"RIFF") else ("jpg", "image/jpeg")
        try:
            r = await self.http.post(self.comfy + "/upload/image", files={"image": (f"inicio-{uuid.uuid4().hex[:8]}.{ext}", blob, mime)}, data={"overwrite": "true"}, timeout=60)
        except httpx.HTTPError as e:
            raise MediaUnavailable(f"ComfyUI inacessivel ({type(e).__name__})")
        if r.status_code != 200:
            raise MediaError("a imagem inicial foi recusada")
        return r.json()["name"]

    async def image(self, jid: str, index: int = 0) -> bytes | None:
        return await self.r.get(f"llmq:media:img:{jid}" + (f":{index}" if index else ""))

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
