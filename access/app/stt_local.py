"""Plano B da transcrição: Whisper (faster-whisper "small", int8) na CPU da VPS quando a GPU do Qwen está fora.

A transcrição normal vai ao Whisper da GPU (llm-queue). Se ele responder 5xx/sem máquina ou der erro de rede, o mesmo áudio é
transcrito aqui, no formato da API da OpenAI (json ou verbose_json com segmentos). Uma transcrição por vez (CPU compartilhada).
O modelo vem dentro da imagem (/srv/whisper), sem baixar nada em produção.
"""
from __future__ import annotations

import os
import tempfile
import threading

_MODELO = None
_TRAVA = threading.Lock()
PASTA = os.environ.get("ACCESS_WHISPER_DIR", "/srv/whisper")
NOME = os.environ.get("ACCESS_WHISPER_MODELO", "small")


def _modelo():
    global _MODELO
    if _MODELO is None:
        from faster_whisper import WhisperModel
        _MODELO = WhisperModel(NOME, device="cpu", compute_type="int8", cpu_threads=int(os.environ.get("ACCESS_WHISPER_THREADS", "6")),
                               download_root=PASTA)
    return _MODELO


def transcrever(audio: bytes, nome: str = "audio.webm", idioma: str | None = "pt", verbose: bool = False) -> dict:
    sufixo = os.path.splitext(nome or "")[1] or ".webm"
    with _TRAVA:
        with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as f:
            f.write(audio)
            caminho = f.name
        try:
            segs, info = _modelo().transcribe(caminho, language=idioma or None, vad_filter=True, beam_size=1)
            segs = list(segs)
        finally:
            try:
                os.unlink(caminho)
            except OSError:
                pass
    texto = " ".join(s.text.strip() for s in segs).strip()
    if not verbose:
        return {"text": texto}
    return {"text": texto, "language": info.language, "duration": info.duration,
            "segments": [{"id": i, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for i, s in enumerate(segs)]}
