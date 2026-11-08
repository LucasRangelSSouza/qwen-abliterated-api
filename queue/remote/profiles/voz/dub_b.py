"""Dublagem, etapa B (depois da traducao): le translated.json, fala cada trecho com a voz clonada do proprio locutor, encaixa no tempo original,
mistura com o fundo e remonta o video. Ambiente `main`.
uso: dub_b.py --work PASTA --lang pt --out saida.mp4 [--metrics m.json] [--ref outra_voz.wav]"""
import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clone import carregar_modelo, preparar_prompt, sintetizar  # noqa: E402
from common import LANGS, agora, duration, escrever_json, has_video, sh, similarity, to_wav  # noqa: E402

SR = 24000
MAX_ACEL = 1.35  # acima disso a fala acelerada fica artificial: deixa passar um pouco do trecho em vez de correr


def ajustar(onda, alvo_s, proximo_s):
    """Acelera (sem mudar o tom) ate MAX_ACEL se a fala nova passa do trecho; devolve (onda, acelerou)."""
    import numpy as np
    dur = len(onda) / SR
    if dur <= alvo_s or alvo_s <= 0:
        return onda, 1.0
    fator = min(MAX_ACEL, dur / alvo_s)
    import soundfile as sf
    import tempfile
    a, b = tempfile.mktemp(suffix=".wav"), tempfile.mktemp(suffix=".wav")
    sf.write(a, onda, SR)
    filtros, f = [], fator
    while f > 2.0:
        filtros.append("atempo=2.0")
        f /= 2.0
    filtros.append(f"atempo={f:.4f}")
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", a, "-filter:a", ",".join(filtros), b])
    out, _ = sf.read(b, dtype="float32")
    os.unlink(a)
    os.unlink(b)
    return np.asarray(out, dtype=np.float32), fator


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ref", default="")
    ap.add_argument("--metrics", default="")
    a = ap.parse_args()
    import numpy as np
    import soundfile as sf
    t0 = agora()
    trad = json.load(open(os.path.join(a.work, "translated.json"), encoding="utf-8"))
    segs = [s for s in trad if s.get("text", "").strip()]
    entrada = glob.glob(os.path.join(a.work, "in.*"))[0]
    video = has_video(entrada)
    total = duration(entrada)
    ref = a.ref or os.path.join(a.work, "ref.wav")
    ref_text = "" if a.ref else open(os.path.join(a.work, "ref.txt"), encoding="utf-8").read()
    model = carregar_modelo()
    prompt, _ = preparar_prompt(model, ref, ref_text)
    t1 = agora()
    wavs, sr = sintetizar(model, prompt, [s["text"] for s in segs], LANGS.get(a.lang.lower()[:2], a.lang))
    assert sr == SR, f"taxa inesperada {sr}"
    t2 = agora()
    faixa = np.zeros(int((total + 2) * SR), dtype=np.float32)
    info = []
    for i, (s, w) in enumerate(zip(segs, wavs)):
        w = np.asarray(w, dtype=np.float32)
        ini = float(s["start"])
        prox = float(segs[i + 1]["start"]) if i + 1 < len(segs) else total
        slot = max(0.3, float(s["end"]) - ini)
        w, fator = ajustar(w, slot, prox - ini)
        limite = max(0.3, prox - ini)  # nunca invade o proximo trecho
        if len(w) / SR > limite:
            w = w[: int(limite * SR)]
        p = int(ini * SR)
        faixa[p:p + len(w)] += w
        info.append({"i": i, "inicio": round(ini, 2), "slot_s": round(slot, 2), "fala_s": round(len(w) / SR, 2), "aceleracao": round(fator, 2)})
    voz = os.path.join(a.work, "dub_voz.wav")
    sf.write(voz, faixa, SR)
    fundo = glob.glob(os.path.join(a.work, "fundo.*"))
    mix = os.path.join(a.work, "dub_mix.wav")
    if fundo:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", voz, "-i", fundo[0], "-filter_complex",
            "[0:a]aresample=44100,pan=stereo|c0=c0|c1=c0[v];[1:a]aresample=44100[b];[v][b]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95", mix])
    else:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", voz, "-ar", "44100", "-ac", "2", mix])
    if video:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", entrada, "-i", mix, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", a.out])
    else:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", mix, a.out])
    m = {"duracao_s": round(duration(a.out), 2), "trechos": len(segs), "carregar_s": round(t1 - t0, 1), "falar_s": round(t2 - t1, 1), "total_s": round(agora() - t0, 1),
         "aceleracao_max": max((x["aceleracao"] for x in info), default=1.0), "ajustes": info[:50], "video": video}
    if a.metrics:
        m["semelhanca_com_original"] = similarity(ref, voz)
        escrever_json(a.metrics, m)
    print({k: v for k, v in m.items() if k != "ajustes"})


if __name__ == "__main__":
    main()
