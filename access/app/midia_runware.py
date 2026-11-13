"""Midia pela Runware no lugar da GPU de midia (decisao do Lucas 13/11/2026: a maquina de midia sai; video e voz pelo Estudio).

Com MEDIA_BACKEND=runware, as rotas que o app ja usa continuam iguais e passam a rodar na Runware:
  POST /v1/videos/generations  recipe=openjutsu-swap (video+foto)  -> efeito trocar-personagem
                               texto/imagem -> video-rapido (qualidade rapida) ou video-ponta (alta, 720p)
  POST /v1/audio/voice-clone   texto + amostra                     -> efeito voz (HeyGen clona a amostra)
  POST /v1/videos/dub, /v1/audio/voice-convert                     -> 503 "em breve" (sem modelo equivalente; ver spec 36 §7)
O acompanhamento e o arquivo saem pelos mesmos caminhos (/v1/media/jobs/{id}, /v1/jobs/{id}/file): os ids sao os jobs do Estudio.
Preco: o do Estudio (painel), cobrado em unidades de credito de midia antes de rodar e devolvido se falhar."""
from __future__ import annotations

import base64
import logging
import struct

import httpx

from app import estudio

log = logging.getLogger("llm-access.midia_runware")

RES_PERSONAGEM = {"480p": "480p", "576p": "768p", "736p": "768p", "768p": "768p", "1080p": "1080p"}
TAMANHO = {  # (formato, resolucao) -> largura x altura aceitos pelo Wan 3.0 / P-Video
    ("paisagem", "480p"): (848, 480), ("retrato", "480p"): (480, 848), ("quadrado", "480p"): (640, 640),
    ("paisagem", "720p"): (1280, 720), ("retrato", "720p"): (720, 1280), ("quadrado", "720p"): (960, 960),
}
EM_BREVE = {"/v1/videos/dub": "A dublagem automática está chegando ao Estúdio. Por enquanto, use a narração com voz clonada e a sincronia labial.",
            "/v1/audio/voice-convert": "A troca de voz está chegando ao Estúdio. Por enquanto, use a narração com voz clonada."}


def ligado(s) -> bool:
    return (s.media_backend or "").lower() == "runware"


def _mime(b64: str, padrao: str) -> str:
    try:
        cab = base64.b64decode(b64[:64] + "=" * (-len(b64[:64]) % 4))
    except ValueError:
        return padrao
    if cab[:4] == b"\x89PNG":
        return "image/png"
    if cab[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if cab[:4] == b"RIFF" and cab[8:12] == b"WEBP":
        return "image/webp"
    if cab[:4] == b"RIFF":
        return "audio/wav"
    if cab[:3] == b"ID3" or cab[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if cab[4:8] == b"ftyp":
        return "audio/mp4" if b"M4A" in cab[8:16] else "video/mp4"
    if cab[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    return padrao


def _data_uri(b64: str, padrao: str) -> str:
    b64 = str(b64 or "")
    return b64 if b64.startswith("data:") else f"data:{_mime(b64, padrao)};base64,{b64}"


def duracao_mp4(dados: bytes) -> float | None:
    """Duracao de MP4/MOV lendo o atomo mvhd (sem ffprobe). None se nao achar."""
    i = dados.find(b"mvhd")
    if i < 4 or i + 32 > len(dados):
        return None
    v = dados[i + 4]
    try:
        if v == 1:
            escala, dur = struct.unpack(">IQ", dados[i + 24:i + 36])
        else:
            escala, dur = struct.unpack(">II", dados[i + 16:i + 24])
    except struct.error:
        return None
    return round(dur / escala, 2) if escala else None


def traduzir(path: str, body: dict) -> tuple[str, dict] | None:
    """Pedido antigo do app -> (efeito, corpo do Estudio). None = rota sem equivalente (em breve)."""
    if path == "/v1/videos/generations" and body.get("recipe") == "openjutsu-swap":
        v = str(body.get("video") or "")
        seg = body.get("seconds")
        if not seg:
            try:
                seg = duracao_mp4(base64.b64decode(v[: 2_000_000] + "=" * (-len(v[: 2_000_000]) % 4)))
            except ValueError:
                seg = None
        return "trocar-personagem", {"segundos": seg or 10, "qualidade": RES_PERSONAGEM.get(str(body.get("resolution")), "480p"),
                                     "inputs": {"video": _data_uri(v, "video/mp4"), "referenceImages": [_data_uri(body.get("image"), "image/jpeg")]}}
    if path == "/v1/videos/generations":
        rapida = body.get("recipe") == "ltx-video" or str(body.get("quality") or "").lower() == "rapida"
        efeito, q = ("video-rapido", "480p") if rapida else ("video-ponta", "720p")
        w, h = TAMANHO.get((str(body.get("format") or "paisagem"), q), TAMANHO[("paisagem", q)])
        prompt = str(body.get("prompt") or "")
        if body.get("negative_prompt"):
            prompt += f"\nAvoid: {str(body['negative_prompt'])[:400]}."
        corpo = {"prompt": prompt, "segundos": body.get("seconds") or 5, "qualidade": q, "largura": w, "altura": h}
        if body.get("image"):
            corpo["inputs"] = {"frameImages": [_data_uri(body["image"], "image/png")]}
        if body.get("seed") is not None:
            corpo["seed"] = int(body["seed"])
        return efeito, corpo
    if path == "/v1/audio/voice-clone":
        corpo = {"texto": str(body.get("text") or ""), "idioma": body.get("language") or "pt"}
        if body.get("reference_audio"):
            corpo["inputs"] = {"audio": _data_uri(body["reference_audio"], "audio/wav")}
        return "voz", corpo
    return None


async def baixar(ctx, job: dict) -> tuple[bytes, str] | None:
    """Baixa o resultado da Runware (URL valida por 7 dias) para entregar no /v1/jobs/{id}/file. Guarda no job para nao baixar 2x."""
    if job.get("bytes"):
        return job["bytes"], job["mime"]
    if not job.get("url"):
        return None
    if job["url"].startswith("https://mock.runware.local/"):
        dados, mime = b"\x00\x00\x00\x18ftypmp42mock", "video/mp4" if job["kind"] == "video" else "audio/mpeg"
    else:
        try:
            r = await ctx.http.get(job["url"], timeout=180, follow_redirects=True)
        except httpx.HTTPError as e:
            log.warning("download do job %s falhou: %s", job["id"], e)
            return None
        if r.status_code != 200:
            return None
        dados = r.content
        mime = r.headers.get("content-type", "").split(";")[0] or ("video/mp4" if job["kind"] == "video" else "audio/mpeg")
    job.update(bytes=dados, mime=mime)
    return dados, mime


def progresso(job: dict, agora: float) -> dict:
    gasto = agora - job["criado"]
    eta = max(0, int(job["tempo_estimado_s"] - gasto))
    st = job["status"]
    prog = 100 if st == "done" else min(95, int(gasto / max(1, job["tempo_estimado_s"]) * 100)) if st == "running" else 0
    rot = "vídeo" if job["kind"] == "video" else "áudio" if job["kind"] == "audio" else "imagem"
    msg = {"running": f"Criando o seu {rot} no Estúdio..." if job["kind"] != "image" else "Criando a sua imagem no Estúdio...",
           "done": "Pronto.", "failed": "Não deu certo. Nada foi cobrado."}[st]
    return {"id": job["id"], "kind": job["kind"], "status": st, "position": None, "eta_s": 0 if st == "done" else eta, "progress": prog, "message": msg,
            "mime": job.get("mime"), "seconds": None, "size": None, "error": job.get("erro")}


def estado(job: dict) -> dict:
    return {"id": job["id"], "state": job["status"], "mime": job.get("mime"), "seconds": None, "size": None, "error": job.get("erro")}


def achar(request, job_id: str, person_id: int | None) -> dict | None:
    j = estudio._jobs(request).get(job_id)
    if j is None or (person_id is not None and j["person_id"] not in (None, person_id)):
        return None
    return j
