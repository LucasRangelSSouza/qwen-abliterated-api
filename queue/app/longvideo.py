"""Video longo: o modelo gera clipes de ate ~4 s; o pedido maior vira uma cadeia (o ultimo quadro de um clipe e o quadro inicial do proximo)
e os clipes sao juntados num MP4 so. O limite e o do modelo por clipe, nao do servico: a duracao total aceita vai ate VIDEO_MAX_TOTAL."""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile


class FfmpegError(RuntimeError):
    pass


async def _ffmpeg(*args: str) -> None:
    p = await asyncio.create_subprocess_exec("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode != 0:
        raise FfmpegError((err or b"").decode("utf-8", "replace")[-300:])


async def last_frame_b64(mp4: bytes) -> str:
    """Ultimo quadro do clipe como PNG em base64 (vira a imagem inicial do proximo clipe)."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.mp4"), os.path.join(d, "last.png")
        with open(src, "wb") as f:
            f.write(mp4)
        await _ffmpeg("-sseof", "-0.15", "-i", src, "-update", "1", "-frames:v", "1", dst)
        with open(dst, "rb") as f:
            return base64.b64encode(f.read()).decode()


async def concat(clips: list[bytes]) -> bytes:
    """Junta MP4s com os mesmos parametros de codificacao (todos saem da mesma receita) sem recodificar."""
    with tempfile.TemporaryDirectory() as d:
        lista = []
        for i, blob in enumerate(clips):
            p = os.path.join(d, f"c{i}.mp4")
            with open(p, "wb") as f:
                f.write(blob)
            lista.append(f"file '{p}'")
        with open(os.path.join(d, "l.txt"), "w") as f:
            f.write("\n".join(lista))
        out = os.path.join(d, "out.mp4")
        await _ffmpeg("-f", "concat", "-safe", "0", "-i", os.path.join(d, "l.txt"), "-c", "copy", "-movflags", "+faststart", out)
        with open(out, "rb") as f:
            return f.read()



async def has_audio(mp4: bytes) -> bool:
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.mp4")
        with open(src, "wb") as f:
            f.write(mp4)
        p = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", src,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await p.communicate()
        return b"audio" in out


async def add_silent_audio(mp4: bytes) -> bytes:
    """O carregador de video do ComfyUI falha em arquivo sem trilha de audio: junta uma trilha muda."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.mp4"), os.path.join(d, "out.mp4")
        with open(src, "wb") as f:
            f.write(mp4)
        await _ffmpeg("-i", src, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest", "-c:v", "copy", "-c:a", "aac", dst)
        with open(dst, "rb") as f:
            return f.read()


async def duration(blob: bytes) -> float:
    """Duracao em segundos de um arquivo de audio ou video (0 se o ffprobe nao entende o arquivo)."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.bin")
        with open(src, "wb") as f:
            f.write(blob)
        p = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await p.communicate()
        try:
            return float(out.decode().strip())
        except ValueError:
            return 0.0
