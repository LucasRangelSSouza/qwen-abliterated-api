"""Dublagem, etapa A (antes da traducao): separa fala e fundo, transcreve a fala com tempos e escolhe a amostra de referencia da voz. Ambiente `main`.
uso: dub_a.py --work PASTA   (le PASTA/in.<ext>; escreve segments.json, vocals.wav, fundo.wav, ref.wav, ref.txt)"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import agora, duration, escrever_json, sh, to_wav, transcribe  # noqa: E402

RUN = "/workspace/voz/run.sh"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--src-lang", default="")
    a = ap.parse_args()
    t0 = agora()
    entrada = glob.glob(os.path.join(a.work, "in.*"))[0]
    full = to_wav(entrada, os.path.join(a.work, "full.wav"), sr=44100, mono=False)
    sh(["bash", RUN, "main", "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs", "-d", "cuda", "-o", a.work, full])
    pasta = os.path.join(a.work, "htdemucs", "full")
    vocals = glob.glob(os.path.join(pasta, "vocals.*"))[0]
    fundo = glob.glob(os.path.join(pasta, "no_vocals.*"))[0]
    os.replace(vocals, os.path.join(a.work, "vocals" + os.path.splitext(vocals)[1]))
    os.replace(fundo, os.path.join(a.work, "fundo" + os.path.splitext(fundo)[1]))
    vocals = glob.glob(os.path.join(a.work, "vocals.*"))[0]
    t1 = agora()
    mono = to_wav(vocals, os.path.join(a.work, "vocals16.wav"), sr=16000)
    segs, idioma = transcribe(mono, a.src_lang or None)
    if not segs:
        raise RuntimeError("nenhuma fala foi detectada no video")
    # referencia da voz: os trechos mais longos (fala continua e limpa), juntos ate ~18 s
    escolhidos, total = [], 0.0
    for s in sorted(segs, key=lambda x: x["end"] - x["start"], reverse=True):
        d = s["end"] - s["start"]
        if total + d > 18 and escolhidos:
            continue
        escolhidos.append(s)
        total += d
        if total >= 12:
            break
    escolhidos.sort(key=lambda x: x["start"])
    partes = []
    for i, s in enumerate(escolhidos):
        p = os.path.join(a.work, f"ref{i}.wav")
        to_wav(vocals, p, sr=24000, ss=round(s["start"], 3), t=round(s["end"] - s["start"], 3))
        partes.append(p)
    lista = os.path.join(a.work, "reflista.txt")
    open(lista, "w", encoding="utf-8").write("".join(f"file '{p}'\n" for p in partes))
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista, os.path.join(a.work, "ref.wav")])
    open(os.path.join(a.work, "ref.txt"), "w", encoding="utf-8").write(" ".join(s["text"] for s in escolhidos))
    escrever_json(os.path.join(a.work, "segments.json"), {"language": idioma, "duration": round(duration(entrada), 2), "segments": segs, "separar_s": round(t1 - t0, 1), "transcrever_s": round(agora() - t1, 1)})
    print({"idioma": idioma, "segmentos": len(segs), "referencia_s": round(total, 1)})


if __name__ == "__main__":
    main()
