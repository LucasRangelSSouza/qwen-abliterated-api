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


def public(eff: dict) -> dict:
    """O que o status do job mostra: tudo menos o texto base64 da imagem inicial."""
    return {k: v for k, v in eff.items() if k != "image"}


def with_style(prompt: str, style: str | None) -> str:
    return f"{prompt}, {STYLES[style]}" if style else prompt
