#!/usr/bin/env python3
"""Audio pipeline test: speech (Windows SAPI, pt-BR) -> whisper.cpp transcription -> this API -> answer checked.

The model itself is a vision-language model (no audio modality); audio therefore goes through speech-to-text first.
Windows-only helper (uses PowerShell System.Speech and a local whisper.cpp build); on Linux swap in `espeak-ng`/`whisper-cli`.
usage: BASE_URL API_KEY python tests/pipeline_audio.py   -> reports/pipeline-audio.json
"""
import json, os, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
import suite  # noqa: E402

WHISPER = os.environ.get("WHISPER_CLI", os.path.expanduser("~/whisper-cpp/bin/Release/whisper-cli.exe"))
MODEL = os.environ.get("WHISPER_MODEL", os.path.expanduser("~/.claude-video-vision/models/ggml-large-v3-turbo.bin"))
CASES = [("Qual é a capital do Brasil e quantos estados o país tem?", ("brasília", "brasilia"), ("26", "vinte e seis")),
         ("Quanto é quinze vezes quatro?", ("60", "sessenta"), ())]


def tts(text, path):
    ps = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SelectVoice('Microsoft Maria Desktop'); $s.SetOutputToWaveFile('%s'); $s.Speak('%s'); $s.Dispose()" % (path, text.replace("'", "")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def transcribe(wav):
    p = subprocess.run([WHISPER, "-m", MODEL, "-l", "pt", "-nt", "-f", wav], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return " ".join(l.strip() for l in p.stdout.splitlines() if l.strip())


rows = []
for said, any_a, any_b in CASES:
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "q.wav")
        t0 = time.time(); tts(said, wav); t_tts = time.time() - t0
        t0 = time.time(); heard = transcribe(wav); t_stt = time.time() - t0
    t0 = time.time()
    r = suite.call([{"role": "user", "content": heard + "\n\nResponda de forma curta."}], think=False, max_tokens=120, stream=False)
    ans = (r.get("text") or "").lower()
    ok = r["status"] == 200 and any(a in ans for a in any_a) and (not any_b or any(b in ans for b in any_b))
    rows.append({"spoken": said, "transcript": heard, "answer": (r.get("text") or "")[:200], "tts_s": round(t_tts, 1), "stt_s": round(t_stt, 1),
                 "llm_s": round(time.time() - t0, 1), "pass": ok})
    print("PASS" if ok else "FAIL", "|", heard, "->", (r.get("text") or "")[:80].replace("\n", " "), flush=True)
os.makedirs("reports", exist_ok=True)
json.dump({"cases": rows, "pass": all(r["pass"] for r in rows)}, open("reports/pipeline-audio.json", "w"), indent=1, ensure_ascii=False)
