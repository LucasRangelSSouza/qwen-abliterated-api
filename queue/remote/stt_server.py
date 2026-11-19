"""Transcricao de voz (ditado) na maquina do LLM: faster-whisper no processador, API compativel com /v1/audio/transcriptions.
Roda em 127.0.0.1:18002 (so local; o llm-queue abre o tunel). Baixa prioridade para nao atrapalhar o vLLM.

Ditado ao vivo (spec 49): WebSocket /v1/audio/stream no MESMO processo e com o MESMO modelo ja carregado (a VM tem pouca RAM livre:
um segundo modelo arriscaria o vLLM). Protocolo: frames binarios PCM 16 bits LE, 16 kHz, mono; texto {"type":"KeepAlive"} e
{"type":"CloseStream"}; volta {"type":"TranscriptInterim"|"TranscriptText","data":"..."} e {"type":"TranscriptEndpoint"}."""
import array
import asyncio
import io
import json
import math
import os
import re
import tempfile
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from starlette.concurrency import run_in_threadpool

MODELO = os.environ.get("STT_MODEL", "large-v3-turbo")
THREADS = int(os.environ.get("STT_THREADS", "16"))
MAX_MB = 25
VERSAO = 3  # sobe quando o codigo muda: o provisionamento reinicia o servico se o /health mostrar outra versao
STREAM_MAX = int(os.environ.get("STT_STREAM_MAX", "24"))  # conexoes de ditado ao vivo ao mesmo tempo
PARALELO = int(os.environ.get("STT_STREAM_PARALELO", "2"))  # decodificacoes de ditado ao mesmo tempo (o resto espera ou pula o parcial)

app = FastAPI()
if os.environ.get("STT_SEM_MODELO") == "1":  # testes: so a logica do fluxo, sem baixar/carregar o Whisper
    modelo = None
else:
    from faster_whisper import WhisperModel

    modelo = WhisperModel(MODELO, device="cpu", compute_type="int8", cpu_threads=THREADS, num_workers=2)


@app.get("/health")
def health():
    return {"ok": True, "model": MODELO, "threads": THREADS, "v": VERSAO, "stream": {"conexoes": _estado["conexoes"], "max": STREAM_MAX}}


# ------------------------------------------------------------------ ditado ao vivo
SR = 16000
QUADRO = 320  # 20 ms de amostras
# frases que o Whisper inventa em trecho quase mudo (legenda de video): nunca viram texto do usuario
ALUCINACAO = re.compile(r"^\W*(legendas? (pela|por) .*|obrigad[oa] (por|pela) assist\w*|inscreva-se.*|amara\.org.*|tchau\W*|\.+)\W*$", re.I)


class Fluxo:
    """Fala x silencio por energia, por conexao. alimentar(pcm) devolve acoes: 'interim' (vale reprocessar o trecho aberto) e
    'final' (a frase acabou: silencio de endpointing_ms ou trecho longo demais)."""

    def __init__(self, endpointing_ms: int = 300, interim_ms: int = 800, max_s: float = 28.0):
        self.fim_q = max(5, endpointing_ms // 20)
        self.interim_q = max(10, interim_ms // 20)
        self.max_q = int(max_s * 50)
        self.fala = array.array("h")
        self.falando = False
        self.silencio_q = 0
        self.novos_q = 0
        self.piso = 200.0
        self.resto = b""

    def _limiar(self) -> float:
        return max(350.0, self.piso * 2.5)

    def alimentar(self, pcm: bytes) -> list[str]:
        acoes: list[str] = []
        dados = self.resto + pcm
        n = len(dados) // (QUADRO * 2) * (QUADRO * 2)
        self.resto = dados[n:]
        for i in range(0, n, QUADRO * 2):
            q = array.array("h")
            q.frombytes(dados[i:i + QUADRO * 2])
            rms = math.sqrt(sum(x * x for x in q) / QUADRO)
            if rms > self._limiar():
                self.falando, self.silencio_q = True, 0
            elif not self.falando:
                self.piso = self.piso * 0.95 + rms * 0.05  # ruido de fundo
                continue
            else:
                self.silencio_q += 1
            self.fala.extend(q)
            self.novos_q += 1
            if self.silencio_q >= self.fim_q or len(self.fala) >= self.max_q * QUADRO:
                acoes.append("final")
            elif self.novos_q >= self.interim_q and self.silencio_q == 0:
                self.novos_q = 0
                acoes.append("interim")
        return acoes

    def audio(self, ultimos_s: float = 15.0):
        """Trecho aberto como float32 (o que o faster-whisper aceita), no maximo os ultimos `ultimos_s` segundos."""
        import numpy as np

        a = self.fala[-int(ultimos_s * SR):]
        return np.frombuffer(a.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0

    def fechar(self):
        a = self.audio(30.0)
        self.fala = array.array("h")
        self.falando, self.silencio_q, self.novos_q = False, 0, 0
        return a

    @property
    def aberto(self) -> bool:
        return self.falando and len(self.fala) > SR // 4


_estado = {"conexoes": 0}
_vaga = asyncio.Semaphore(PARALELO)


def _decodificar(audio, idioma: str) -> str:
    segs, _ = modelo.transcribe(audio, language=idioma or None, beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                without_timestamps=True)
    t = " ".join(s.text.strip() for s in segs).strip()
    return "" if ALUCINACAO.match(t) else t


@app.websocket("/v1/audio/stream")
async def ditado_ao_vivo(ws: WebSocket):
    q = ws.query_params
    await ws.accept()
    if q.get("encoding", "linear16") != "linear16" or q.get("sample_rate", "16000") != "16000" or q.get("channels", "1") != "1":
        await ws.send_text(json.dumps({"type": "Error", "data": "use linear16, 16000 Hz, 1 canal"}))
        return await ws.close(1003)
    if _estado["conexoes"] >= STREAM_MAX:
        await ws.send_text(json.dumps({"type": "Error", "data": "ditado ao vivo lotado"}))
        return await ws.close(1013)
    _estado["conexoes"] += 1
    idioma = q.get("language") or "pt"
    fluxo = Fluxo(endpointing_ms=int(q.get("endpointing_ms") or 300))
    parcial = ""

    async def final():
        nonlocal parcial
        audio = fluxo.fechar()
        async with _vaga:  # o final nunca e pulado: espera a vez
            t = await run_in_threadpool(_decodificar, audio, idioma)
        parcial = ""
        if t:
            await ws.send_text(json.dumps({"type": "TranscriptText", "data": t}, ensure_ascii=False))
        await ws.send_text(json.dumps({"type": "TranscriptEndpoint"}))

    try:
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout=15)  # sem audio nem KeepAlive por 15 s: fecha
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                for acao in fluxo.alimentar(msg["bytes"]):
                    if acao == "final":
                        await final()
                    elif acao == "interim" and not _vaga.locked():  # servidor ocupado: pula o parcial (o final vem depois)
                        async with _vaga:
                            t = await run_in_threadpool(_decodificar, fluxo.audio(), idioma)
                        if t and t != parcial:
                            parcial = t
                            await ws.send_text(json.dumps({"type": "TranscriptInterim", "data": t}, ensure_ascii=False))
            elif msg.get("text"):
                try:
                    tipo = json.loads(msg["text"]).get("type")
                except Exception:  # noqa: BLE001
                    tipo = ""
                if tipo == "CloseStream":
                    if fluxo.aberto:
                        await final()
                    break
    except asyncio.TimeoutError:
        pass
    except Exception:  # noqa: BLE001  conexao caiu no meio: so libera a vaga
        pass
    finally:
        _estado["conexoes"] -= 1
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


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
