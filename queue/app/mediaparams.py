"""Parametros de imagem e video: validacao explicita, limites seguros, defaults bons e os valores EFETIVOS devolvidos no status do job.
Valor fora da faixa e ajustado (e listado em `adjusted`); valor de tipo errado ou de lista fechada invalido recusa o pedido com mensagem clara."""
from __future__ import annotations

import base64
import math
import binascii
import random

SAMPLERS = ("euler", "euler_ancestral", "heun", "dpm_2", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde", "lms", "lcm", "uni_pc", "ddim", "res_multistep")
SCHEDULERS = ("simple", "normal", "karras", "sgm_uniform", "exponential", "beta", "ddim_uniform")
STYLES = {
    "foto": "photorealistic, natural light, sharp focus, high detail",
    "cinema": "cinematic still, dramatic lighting, shallow depth of field, film grain",
    "ilustracao": "digital illustration, clean lines, vibrant colors",
    "anime": "anime style, detailed, vibrant colors",
    "3d": "3d render, soft studio lighting, high detail",
    "minimalista": "minimalist, flat design, simple shapes, plenty of negative space",
    "aquarela": "watercolor painting, soft edges, paper texture",
}
IMG_MAX_PIXELS = 1_650_000
IMG_MAX_N = 4
VIDEO_MAX_PIXELS = 512 * 512
VIDEO_MAX_SECONDS = 4.0
VIDEO_MAX_FRAMES = 121
VIDEO_MAX_TOTAL = 60.0  # duracao total aceita: acima de VIDEO_MAX_SECONDS vira cadeia de clipes (app/longvideo.py)
VIDEO_SIZES = {"paisagem": (640, 384), "retrato": (384, 640), "quadrado": (512, 512)}
NEG_VIDEO = "worst quality, inconsistent motion, blurry, jittery, distorted, watermark, text, deformed"
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PROMPT_CHARS = 4000


class ParamError(ValueError):
    """Parametro invalido (mensagem em portugues, sem jargao de infraestrutura)."""


def _num(p: dict, names: tuple[str, ...], default, lo, hi, adjusted: list, label: str, integer: bool = False):
    raw = next((p[n] for n in names if p.get(n) is not None and p.get(n) != ""), None)
    if raw is None:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ParamError(f"Parâmetro inválido: {label} deve ser um número.")
    if v != v or v in (float("inf"), float("-inf")):
        raise ParamError(f"Parâmetro inválido: {label} deve ser um número.")
    v = int(round(v)) if integer else round(v, 3)
    c = max(lo, min(hi, v))
    if c != v:
        adjusted.append(label)
    return c


def _choice(p: dict, name: str, options: tuple[str, ...], default: str) -> str:
    v = p.get(name)
    if v is None or v == "":
        return default
    v = str(v).strip().lower()
    if v not in options:
        raise ParamError(f"Parâmetro inválido: {name} deve ser um de {', '.join(options)}.")
    return v


def _wh(p: dict, step: int, lo: int, hi: int, default: tuple[int, int], max_pixels: int, adjusted: list) -> tuple[int, int]:
    w, h = default
    sz = str(p.get("size") or "")
    if "x" in sz:
        try:
            a, b = sz.lower().split("x", 1)
            w, h = int(a), int(b)
        except ValueError:
            raise ParamError("Parâmetro inválido: size deve ser como 1024x1024.")
    w = _num(p, ("width", "largura"), w, 1, 100000, [], "width", True)
    h = _num(p, ("height", "altura"), h, 1, 100000, [], "height", True)
    rw, rh = max(lo, min(hi, round(w / step) * step)), max(lo, min(hi, round(h / step) * step))
    while rw * rh > max_pixels:  # reduz mantendo o limite de memoria da GPU
        rw, rh = max(lo, rw - step), max(lo, rh - step)
        if rw == lo and rh == lo:
            break
    if (rw, rh) != (w, h):
        adjusted.append("size")
    return rw, rh


def _seed(p: dict) -> int:
    if p.get("seed") is None or p.get("seed") == "":
        return random.randint(0, 2**53)
    try:
        return max(0, min(2**53, int(p["seed"])))
    except (TypeError, ValueError):
        raise ParamError("Parâmetro inválido: seed deve ser um número inteiro.")


def _prompt(p: dict) -> str:
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        raise ParamError("O pedido precisa de um prompt.")
    return prompt[:MAX_PROMPT_CHARS]


def normalize_image(p: dict) -> tuple[dict, list]:
    adj: list = []
    style = p.get("style") or p.get("estilo")
    if style not in (None, "") and str(style).lower() not in STYLES:
        raise ParamError(f"Parâmetro inválido: style deve ser um de {', '.join(STYLES)}.")
    style = str(style).lower() if style else None
    w, h = _wh(p, 64, 256, 1536, (1024, 1024), IMG_MAX_PIXELS, adj)
    eff = {
        "prompt": _prompt(p), "negative_prompt": str(p.get("negative_prompt") or "")[:MAX_PROMPT_CHARS],
        "width": w, "height": h,
        "steps": _num(p, ("steps",), 4, 1, 12, adj, "steps", True),
        "cfg": _num(p, ("cfg", "guidance"), 1.0, 0.5, 6.0, adj, "cfg"),
        "seed": _seed(p), "sampler": _choice(p, "sampler", SAMPLERS, "euler"), "scheduler": _choice(p, "scheduler", SCHEDULERS, "simple"),
        "n": _num(p, ("n", "quantidade"), 1, 1, IMG_MAX_N, adj, "n", True), "style": style,
    }
    return eff, adj


def normalize_video(p: dict) -> tuple[dict, list]:
    adj: list = []
    fmt = str(p.get("format") or p.get("formato") or "paisagem").lower()
    if fmt not in VIDEO_SIZES:
        raise ParamError(f"Parâmetro inválido: format deve ser um de {', '.join(VIDEO_SIZES)}.")
    w, h = _wh(p, 32, 256, 832, VIDEO_SIZES[fmt], VIDEO_MAX_PIXELS, adj)
    total = _num(p, ("seconds", "segundos"), 4.0, 1.0, VIDEO_MAX_TOTAL, adj, "seconds")
    segments = max(1, math.ceil(total / VIDEO_MAX_SECONDS - 1e-9))
    secs = total / segments  # cada clipe leva a mesma parte; ate 4 s por clipe
    fps = _num(p, ("fps",), 25, 8, 30, adj, "fps", True)
    frames = int(round(secs * fps / 8)) * 8 + 1  # o modelo pede 8n+1 quadros
    if frames > VIDEO_MAX_FRAMES:
        frames = (VIDEO_MAX_FRAMES - 1) // 8 * 8 + 1
        adj.append("seconds")
    image = p.get("image") or p.get("start_image")
    if image:
        raw = str(image)
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            raise ParamError("Parâmetro inválido: image deve ser uma imagem em base64.")
        if not blob.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
            raise ParamError("Parâmetro inválido: image deve ser PNG, JPEG ou WEBP.")
        if len(blob) > MAX_IMAGE_BYTES:
            raise ParamError("Parâmetro inválido: a imagem inicial é grande demais (máximo 6 MB).")
    eff = {
        "prompt": _prompt(p), "negative_prompt": str(p.get("negative_prompt") or NEG_VIDEO)[:MAX_PROMPT_CHARS],
        "width": w, "height": h, "seconds": round(frames / fps, 2), "fps": fps, "frames": frames, "segments": segments, "total_seconds": round(frames / fps * segments, 2),
        "steps": _num(p, ("steps",), 30, 10, 50, adj, "steps", True), "cfg": _num(p, ("cfg", "guidance"), 3.0, 1.0, 8.0, adj, "cfg"),
        "seed": _seed(p), "sampler": _choice(p, "sampler", SAMPLERS, "euler"),
        "image_to_video": bool(image), "strength": _num(p, ("strength",), 1.0, 0.0, 1.0, adj, "strength") if image else None,
    }
    return eff, adj

SWAP_RESOLUTIONS = {"480p": (864, 480), "576p": (1024, 576), "736p": (1312, 736)}
MAX_SWAP_VIDEO_BYTES = 40 * 1024 * 1024


def _blob(raw, what: str, limit: int) -> bytes:
    if not raw:
        raise ParamError(f"Parâmetro inválido: {what} é obrigatório (arquivo em base64).")
    raw = str(raw)
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise ParamError(f"Parâmetro inválido: {what} deve estar em base64.")
    if len(blob) > limit:
        raise ParamError(f"Parâmetro inválido: {what} é grande demais (máximo {limit // (1024 * 1024)} MB).")
    return blob


def swap_blobs(p: dict) -> tuple[bytes, bytes]:
    """(video de origem, foto do personagem) ja decodificados e validados."""
    video = _blob(p.get("video") or p.get("source_video"), "video", MAX_SWAP_VIDEO_BYTES)
    if not (video[4:8] == b"ftyp" or video.startswith(b"\x1a\x45\xdf\xa3")):
        raise ParamError("Parâmetro inválido: video deve ser MP4, MOV ou WEBM.")
    image = _blob(p.get("image") or p.get("character_image"), "image", MAX_IMAGE_BYTES)
    if not image.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
        raise ParamError("Parâmetro inválido: image deve ser PNG, JPEG ou WEBP.")
    return video, image


def normalize_swap(p: dict) -> tuple[dict, list]:
    """Troca de personagem em video (Openjutsu / MiniMax H3): a pessoa principal do video vira a da foto, mantendo camera, cortes e audio."""
    adj: list = []
    swap_blobs(p)  # valida cedo; o conteudo nao entra no status do job
    res = str(p.get("resolution") or p.get("resolucao") or "480p").lower()
    if res not in SWAP_RESOLUTIONS:
        raise ParamError(f"Parâmetro inválido: resolution deve ser um de {', '.join(SWAP_RESOLUTIONS)}.")
    w, h = SWAP_RESOLUTIONS[res]
    eff = {
        "prompt": str(p.get("prompt") or "troca de personagem")[:300], "negative_prompt": "", "width": w, "height": h, "seconds": 0, "fps": 24, "frames": 0,
        "segments": 1, "total_seconds": 0, "resolution": res, "steps": _num(p, ("steps",), 4, 1, 8, adj, "steps", True),
        "clip_seconds": _num(p, ("clip_seconds",), 6.0, 2.5, 15.0, adj, "clip_seconds"), "seed": _seed(p) % 4294967296,  # o no do Openjutsu so aceita 32 bits
        "no_lip_sync": bool(p.get("no_lip_sync", True)), "image_to_video": False, "strength": None, "cfg": None, "sampler": None,
    }
    return eff, adj


def public(eff: dict) -> dict:
    """O que o status do job mostra: tudo menos o texto base64 da imagem inicial."""
    return {k: v for k, v in eff.items() if k != "image"}


def with_style(prompt: str, style: str | None) -> str:
    return f"{prompt}, {STYLES[style]}" if style else prompt


# --- Wan 2.2 A14B (worker sob demanda numa RTX PRO 6000 de 96 GB). Medido em 08/11/2026: 81 quadros em 832x480 = 254 s; em 1280x720 = 852 s.
WAN14_FPS = 16
WAN14_CLIP_FRAMES = 81  # 5,06 s por clipe (o que o modelo foi treinado para fazer)
WAN14_SIZES = {"alta": {"paisagem": (1280, 720), "retrato": (720, 1280), "quadrado": (960, 960)},
               "padrao": {"paisagem": (832, 480), "retrato": (480, 832), "quadrado": (640, 640)}}
WAN14_SECONDS_PER_CLIP = {"alta": 860.0, "padrao": 260.0}  # tempo medido de um clipe cheio, ja contando carregar os modelos
NEG_WAN14 = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
             "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")


def wan14_eta_s(eff: dict) -> int:
    """Tempo esperado de geracao (sem contar a partida da maquina): clipes x tempo medido do clipe, proporcional aos quadros."""
    per = WAN14_SECONDS_PER_CLIP.get(eff.get("quality") or "padrao", 260.0)
    return int(per * int(eff.get("segments") or 1) * (int(eff.get("frames") or WAN14_CLIP_FRAMES) / WAN14_CLIP_FRAMES))


def normalize_video_wan14(p: dict) -> tuple[dict, list]:
    """Wan 2.2 14B (qualidade maxima, 16 fps, clipes de ate 5 s). `quality` = alta (720p, ~14 min por clipe) | padrao (480p, ~4 min) | auto (padrao: video acima de 5 s;
    alta: video de ate 5 s, para a producao inteira caber em ~20 min). Video maior que 5 s vira cadeia: o ultimo quadro de um clipe e a imagem inicial do proximo."""
    adj: list = []
    fmt = str(p.get("format") or p.get("formato") or "paisagem").lower()
    if fmt not in WAN14_SIZES["alta"]:
        raise ParamError(f"Parâmetro inválido: format deve ser um de {', '.join(WAN14_SIZES['alta'])}.")
    total = _num(p, ("seconds", "segundos"), 5.0, 1.0, VIDEO_MAX_TOTAL, adj, "seconds")
    clip_max = (WAN14_CLIP_FRAMES - 1) / WAN14_FPS + 0.07  # 81 quadros duram 5,06 s: esse e o teto de UM clipe
    segments = max(1, math.ceil(total / clip_max - 1e-9))
    quality = _choice(p, "quality", ("auto", "alta", "padrao"), "auto")
    if quality == "auto":
        quality = "alta" if segments == 1 else "padrao"
    w, h = WAN14_SIZES[quality][fmt]
    secs = total / segments
    frames = min(int(round(secs * WAN14_FPS / 4)) * 4 + 1, WAN14_CLIP_FRAMES)  # o modelo pede 4n+1 quadros
    image = p.get("image") or p.get("start_image")
    if image:
        raw = str(image)
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            raise ParamError("Parâmetro inválido: image deve ser uma imagem em base64.")
        if not blob.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
            raise ParamError("Parâmetro inválido: image deve ser PNG, JPEG ou WEBP.")
        if len(blob) > MAX_IMAGE_BYTES:
            raise ParamError("Parâmetro inválido: a imagem inicial é grande demais (máximo 6 MB).")
    eff = {
        # o negativo do cliente SOMA ao oficial do Wan (que cobre mao, rosto, membros e "tres pernas"); substituir perderia essa protecao
        "prompt": _prompt(p), "negative_prompt": ", ".join(x for x in (str(p.get("negative_prompt") or "").strip()[:MAX_PROMPT_CHARS], NEG_WAN14) if x),
        "width": w, "height": h, "quality": quality, "seconds": round(frames / WAN14_FPS, 2), "fps": WAN14_FPS, "frames": frames, "segments": segments,
        "total_seconds": round(frames / WAN14_FPS * segments, 2),
        "steps": _num(p, ("steps",), 20, 8, 40, adj, "steps", True), "cfg": _num(p, ("cfg", "guidance"), 3.5, 1.0, 8.0, adj, "cfg"),
        "seed": _seed(p), "sampler": "euler", "image_to_video": bool(image), "strength": None,
    }
    return eff, adj


WAN_SIZES = {"paisagem": (1280, 704), "retrato": (704, 1280), "quadrado": (960, 960)}
WAN_CLIP_FRAMES = 121  # 5 s a 24 fps por clipe (o que cabe numa 3090 de 24 GB); acima disso vira cadeia de clipes
WAN_FPS = 24


def normalize_video_wan(p: dict) -> tuple[dict, list]:
    """Wan 2.2 5B (alta qualidade, 24 fps, ate 1280x704). Pedido maior que 5 s vira cadeia: o ultimo quadro de um clipe e a imagem inicial do proximo."""
    adj: list = []
    fmt = str(p.get("format") or p.get("formato") or "paisagem").lower()
    if fmt not in WAN_SIZES:
        raise ParamError(f"Parâmetro inválido: format deve ser um de {', '.join(WAN_SIZES)}.")
    w, h = WAN_SIZES[fmt]
    total = _num(p, ("seconds", "segundos"), 5.0, 1.0, VIDEO_MAX_TOTAL, adj, "seconds")
    clip_max = WAN_CLIP_FRAMES / WAN_FPS  # 121 quadros duram 5,04 s: esse e o teto de UM clipe (com (n-1)/fps o clipe cheio era dividido de novo)
    segments = max(1, math.ceil(total / clip_max - 1e-9))
    secs = total / segments
    frames = int(round(secs * WAN_FPS / 4)) * 4 + 1  # o modelo pede 4n+1 quadros
    frames = min(frames, WAN_CLIP_FRAMES)
    image = p.get("image") or p.get("start_image")
    if image:
        raw = str(image)
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            raise ParamError("Parâmetro inválido: image deve ser uma imagem em base64.")
        if not blob.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
            raise ParamError("Parâmetro inválido: image deve ser PNG, JPEG ou WEBP.")
        if len(blob) > MAX_IMAGE_BYTES:
            raise ParamError("Parâmetro inválido: a imagem inicial é grande demais (máximo 6 MB).")
    eff = {
        "prompt": _prompt(p), "negative_prompt": str(p.get("negative_prompt") or NEG_VIDEO)[:MAX_PROMPT_CHARS],
        "width": w, "height": h, "seconds": round(frames / WAN_FPS, 2), "fps": WAN_FPS, "frames": frames, "segments": segments,
        "total_seconds": round(frames / WAN_FPS * segments, 2),
        "steps": _num(p, ("steps",), 20, 8, 40, adj, "steps", True), "cfg": _num(p, ("cfg", "guidance"), 5.0, 1.0, 8.0, adj, "cfg"),
        "seed": _seed(p), "sampler": "euler", "image_to_video": bool(image), "strength": None,
    }
    return eff, adj
