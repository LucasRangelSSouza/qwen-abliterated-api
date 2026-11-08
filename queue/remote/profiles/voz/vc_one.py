"""Conversao de voz com o Chatterbox VC (MIT). Ambiente `cb`. Carrega o modelo UMA vez e converte varios trechos.
uso: vc_one.py alvo.wav manifesto.json   (manifesto = [{"src": "...", "out": "..."}, ...])"""
import json
import sys

import torchaudio as ta
from chatterbox.vc import ChatterboxVC

alvo, manifesto = sys.argv[1], sys.argv[2]
vc = ChatterboxVC.from_pretrained(device="cuda")
for item in json.load(open(manifesto, encoding="utf-8")):
    wav = vc.generate(item["src"], target_voice_path=alvo)
    ta.save(item["out"], wav.cpu(), vc.sr)
print("vc ok", vc.sr)
