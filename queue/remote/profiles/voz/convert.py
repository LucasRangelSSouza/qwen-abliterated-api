"""Troca de voz de um audio ou video: separa a fala do fundo (demucs), converte a fala para a voz da amostra (Chatterbox VC) e remonta. Ambiente `main`.
uso: convert.py --in entrada.(wav|mp3|mp4|mov) --target amostra.wav --out saida.(wav|mp4) [--keep-bg 1] [--metrics m.json]"""
import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import agora, duration, escrever_json, has_video, sh, similarity, split_points, to_wav  # noqa: E402

RUN = "/workspace/voz/run.sh"


def separar(wav, pasta):
    """demucs htdemucs, 2 trilhas: vocals e no_vocals (fundo)."""
    sh(["bash", RUN, "main", "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs", "-d", "cuda", "-o", pasta, wav])
    base = os.path.splitext(os.path.basename(wav))[0]
    voc = glob.glob(os.path.join(pasta, "htdemucs", base, "vocals.*"))
    fundo = glob.glob(os.path.join(pasta, "htdemucs", base, "no_vocals.*"))
    if not voc or not fundo:
        raise RuntimeError("a separacao de fundo nao gerou os arquivos")
    return voc[0], fundo[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-bg", type=int, default=1)
    ap.add_argument("--metrics", default="")
    a = ap.parse_args()
    t0 = agora()
    work = os.path.join(os.path.dirname(os.path.abspath(a.out)), "cv")
    os.makedirs(work, exist_ok=True)
    video = has_video(a.inp)
    full = to_wav(a.inp, os.path.join(work, "full.wav"), sr=44100, mono=False)
    if a.keep_bg:
        vocals, fundo = separar(full, work)
    else:
        vocals, fundo = full, None
    t1 = agora()
    alvo = to_wav(a.target, os.path.join(work, "alvo.wav"), sr=24000, t=25)
    mono = to_wav(vocals, os.path.join(work, "vocals_mono.wav"), sr=24000)
    cortes = split_points(mono, 25.0)
    manifesto, saidas = [], []
    for i, (ini, fim) in enumerate(cortes):
        src = to_wav(mono, os.path.join(work, f"src{i:03d}.wav"), sr=24000, ss=round(ini, 3), t=round(fim - ini, 3))
        out = os.path.join(work, f"vc{i:03d}.wav")
        manifesto.append({"src": src, "out": out})
        saidas.append(out)
    import json
    escrever_json(os.path.join(work, "manifesto.json"), manifesto)
    sh(["bash", RUN, "cb", "/workspace/voz/vc_one.py", alvo, os.path.join(work, "manifesto.json")])
    lista = os.path.join(work, "lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        f.write("".join(f"file '{p}'\n" for p in saidas))
    conv = os.path.join(work, "conv.wav")
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista, "-ar", "44100", "-ac", "2", conv])
    t2 = agora()
    if fundo:
        mix = os.path.join(work, "mix.wav")
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", conv, "-i", fundo, "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95", mix])
    else:
        mix = conv
    if video:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", a.inp, "-i", mix, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", a.out])
    else:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", mix, a.out])
    m = {"duracao_s": round(duration(a.out), 2), "trechos": len(cortes), "separar_s": round(t1 - t0, 1), "converter_s": round(t2 - t1, 1), "total_s": round(agora() - t0, 1), "video": video}
    if a.metrics:
        m["semelhanca_com_alvo"] = similarity(alvo, conv)
        m["semelhanca_com_original"] = similarity(mono, conv)
        escrever_json(a.metrics, m)
    print(m)


if __name__ == "__main__":
    main()
