"""Clonagem de voz: le um texto com a voz de uma amostra (Qwen3-TTS 1.7B Base, Apache-2.0). Ambiente `main`.
uso: clone.py --text texto.txt --lang pt --ref amostra.wav [--ref-text amostra.txt] --out saida.wav [--metrics m.json]"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import LANGS, agora, duration, escrever_json, similarity, to_wav, transcribe  # noqa: E402


def frases(texto, limite=240):
    """Divide em frases e junta ate o limite: pedacos curtos soam melhor e nao estouram o modelo."""
    partes = [p.strip() for p in re.split(r"(?<=[.!?…])\s+|\n+", texto) if p.strip()]
    out, atual = [], ""
    for p in partes:
        if atual and len(atual) + len(p) + 1 > limite:
            out.append(atual)
            atual = p
        else:
            atual = (atual + " " + p).strip()
    if atual:
        out.append(atual)
    return out or [texto.strip()]


def carregar_modelo():
    import torch
    from qwen_tts import Qwen3TTSModel
    return Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base", device_map="cuda:0", dtype=torch.bfloat16)


def preparar_prompt(model, ref_wav, ref_text):
    if not ref_text.strip():
        segs, _ = transcribe(ref_wav)
        ref_text = " ".join(s["text"] for s in segs)
    return model.create_voice_clone_prompt(ref_audio=ref_wav, ref_text=ref_text, x_vector_only_mode=False), ref_text


def sintetizar(model, prompt, textos, idioma):
    """Devolve (lista de ondas, taxa) uma por texto."""
    wavs, sr = model.generate_voice_clone(text=textos, language=[idioma] * len(textos), voice_clone_prompt=prompt)
    return wavs, sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--lang", default="pt")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--metrics", default="")
    a = ap.parse_args()
    t0 = agora()
    import numpy as np
    import soundfile as sf
    idioma = LANGS.get(a.lang.lower()[:2], a.lang)
    texto = open(a.text, encoding="utf-8").read().strip()
    ref = os.path.join(os.path.dirname(a.out) or ".", "ref24.wav")
    to_wav(a.ref, ref, sr=24000, t=25)  # amostras acima de ~25 s so atrasam; 3 a 20 s bastam
    ref_text = open(a.ref_text, encoding="utf-8").read() if a.ref_text and os.path.exists(a.ref_text) else ""
    model = carregar_modelo()
    prompt, ref_text = preparar_prompt(model, ref, ref_text)
    t1 = agora()
    pedacos = frases(texto)
    wavs, sr = sintetizar(model, prompt, pedacos, idioma)
    silencio = np.zeros(int(sr * 0.18), dtype=np.float32)
    audio = np.concatenate([x for w in wavs for x in (np.asarray(w, dtype=np.float32), silencio)][:-1])
    sf.write(a.out, audio, sr)
    gen = agora() - t1
    dur = len(audio) / sr
    m = {"duracao_s": round(dur, 2), "referencia_s": round(duration(ref), 2), "carregar_s": round(t1 - t0, 1), "gerar_s": round(gen, 1), "rtf": round(gen / max(dur, 0.1), 3),
         "pedacos": len(pedacos), "referencia_texto": ref_text[:200]}
    if a.metrics:
        m["semelhanca"] = similarity(ref, a.out)
        escrever_json(a.metrics, m)
    print(m)


if __name__ == "__main__":
    main()
