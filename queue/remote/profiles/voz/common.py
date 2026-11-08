"""Utilidades comuns da pilha de voz (ambiente `main`). Tudo por arquivos em disco: cada etapa le e escreve na pasta de trabalho do pedido."""
import json
import os
import re
import subprocess
import time

LANGS = {"pt": "Portuguese", "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian"}
_whisper = None


def sh(cmd, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"comando falhou ({r.returncode}): {str(cmd)[:200]} :: {r.stderr[-1200:]}")
    return r.stdout


def duration(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).strip() or 0)


def has_video(path):
    return "video" in sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path])


def to_wav(src, dst, sr=24000, mono=True, ss=None, t=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    cmd += ["-i", src]
    if t is not None:
        cmd += ["-t", str(t)]
    cmd += ["-vn", "-ac", "1" if mono else "2", "-ar", str(sr), dst]
    sh(cmd)
    return dst


def split_points(path, max_s=25.0, min_s=4.0):
    """Divide o audio em trechos de ate max_s cortando em silencios (o conversor de voz tem limite de tamanho de entrada)."""
    total = duration(path)
    if total <= max_s:
        return [(0.0, total)]
    r = subprocess.run(["ffmpeg", "-i", path, "-af", "silencedetect=noise=-35dB:d=0.25", "-f", "null", "-"], capture_output=True, text=True)
    sil = [(float(a), float(b)) for a, b in zip(re.findall(r"silence_start: ([\d.]+)", r.stderr), re.findall(r"silence_end: ([\d.]+)", r.stderr))]
    mids = [(a + b) / 2 for a, b in sil]
    cuts, start = [], 0.0
    while total - start > max_s:
        cand = [m for m in mids if start + min_s <= m <= start + max_s]
        cut = max(cand) if cand else start + max_s
        cuts.append((start, cut))
        start = cut
    cuts.append((start, total))
    return cuts


def _onda_16k(path):
    """Qualquer arquivo de audio/video -> onda float32 mono 16 kHz (via ffmpeg). O faster-whisper aceita a onda direto e nao precisa do PyAV."""
    import numpy as np
    import soundfile as sf
    tmp = path + ".16k.wav"
    to_wav(path, tmp, sr=16000)
    onda, _ = sf.read(tmp, dtype="float32")
    return np.asarray(onda, dtype=np.float32)


def transcribe(path, language=None):
    """faster-whisper large-v3-turbo. GPU quando possivel; sem GPU (ou sem as bibliotecas CUDA) cai para CPU int8 em vez de falhar."""
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        try:
            _whisper = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
        except Exception:  # noqa: BLE001
            _whisper = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    audio = _onda_16k(path)
    try:
        segs, info = _whisper.transcribe(audio, language=language, vad_filter=True, beam_size=5)
        out = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segs if s.text.strip()]
    except Exception:  # noqa: BLE001
        _whisper = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
        segs, info = _whisper.transcribe(audio, language=language, vad_filter=True, beam_size=5)
        out = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segs if s.text.strip()]
    return out, info.language


def similarity(a, b):
    """Semelhanca de voz (cosseno do embedding ECAPA, 0 a 1): so para QA e metricas; devolve None se o modelo nao estiver disponivel."""
    try:
        import torch
        import torchaudio
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except Exception:  # noqa: BLE001
            from speechbrain.pretrained import EncoderClassifier
        enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir="/workspace/voz/ecapa", run_opts={"device": "cpu"})

        def emb(p):
            w, sr = torchaudio.load(p)
            w = w.mean(0, keepdim=True)
            if sr != 16000:
                w = torchaudio.functional.resample(w, sr, 16000)
            return enc.encode_batch(w).squeeze()
        return float(torch.nn.functional.cosine_similarity(emb(a), emb(b), dim=0))
    except Exception:  # noqa: BLE001
        return None


def escrever_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def agora():
    return time.time()
