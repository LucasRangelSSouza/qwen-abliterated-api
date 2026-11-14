"""Transcricao de voz (ditado) na maquina do LLM: faster-whisper no processador, API compativel com /v1/audio/transcriptions.
Roda em 127.0.0.1:18002 (so local; o llm-queue abre o tunel). Baixa prioridade para nao atrapalhar o vLLM."""
import io
import os
import tempfile
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

MODELO = os.environ.get("STT_MODEL", "large-v3-turbo")
THREADS = int(os.environ.get("STT_THREADS", "16"))
MAX_MB = 25
VERSAO = 2  # sobe quando o codigo muda: o provisionamento reinicia o servico se o /health mostrar outra versao

app = FastAPI()
modelo = WhisperModel(MODELO, device="cpu", compute_type="int8", cpu_threads=THREADS, num_workers=2)


@app.get("/health")
def health():
    return {"ok": True, "model": MODELO, "threads": THREADS, "v": VERSAO}


@app.post("/v1/audio/transcriptions")
async def transcrever(file: UploadFile = File(...), language: str = Form("pt"), model: str = Form(""), prompt: str = Form(""),
                      response_format: str = Form("json")):
    dados = await file.read()
    if len(dados) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, "arquivo grande demais")
    t0 = time.time()
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "a.wav")[1] or ".wav", delete=True) as f:
        f.write(dados)
        f.flush()
        segs, info = modelo.transcribe(
            f.name,
            language=language or None,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            word_timestamps=response_format == "verbose_json",
        )
        segs = list(segs)
        texto = " ".join(s.text.strip() for s in segs).strip()
    out = {"text": texto, "language": info.language, "duration": round(info.duration, 2), "took_s": round(time.time() - t0, 2)}
    if response_format == "verbose_json":
        # tempos por frase e por palavra (cortes de video, legendas): mesmo formato da API da OpenAI
        out["segments"] = [{"id": i, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for i, s in enumerate(segs)]
        out["words"] = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()} for s in segs for w in (s.words or [])]
    return out
