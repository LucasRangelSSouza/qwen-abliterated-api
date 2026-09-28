#!/usr/bin/env python3
"""Audio pipeline through the deployed solution only: speech -> POST /v1/audio/transcriptions (whisper sidecar) ->
POST /v1/chat/completions (Qwen) -> answer checked.

Speech is synthesised locally with Windows SAPI (pt-BR voice) so the test needs no audio fixture other than what it
generates; on Linux, use the committed tests/fixtures/speech-pt.wav for a single case.
usage: BASE_URL API_KEY python tests/pipeline_audio.py   -> reports/ops/pipeline-audio.json
"""
import json, os, subprocess, sys, tempfile, time, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import suite  # noqa: E402

CASES = [("Qual é a capital do Brasil e quantos estados o país tem?", ("brasilia",), ("26", "vinte e seis")),
         ("Quanto é quinze vezes quatro?", ("60", "sessenta"), ()),
         ("Escreva uma função em Python que soma dois números.", ("def ", "return"), ())]


def tts(text, path):
    ps = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('Microsoft Maria Desktop'); "
          "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono); "
          "$s.SetOutputToWaveFile('%s',$f); $s.Speak('%s'); $s.Dispose()" % (path, text.replace("'", "")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def transcribe(wav_path):
    boundary = "----pipeline" + str(int(time.time() * 1000))
    parts = []
    for name, val in (("model", "whisper"), ("language", "pt"), ("response_format", "json")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.append(open(wav_path, "rb").read())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(suite.BASE + "/audio/transcriptions", b"".join(parts),
                                 {"Authorization": "Bearer " + suite.KEY, "Content-Type": "multipart/form-data; boundary=" + boundary})
    return json.load(urllib.request.urlopen(req, timeout=120))["text"].strip()


def norm(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


rows = []
for said, must_any, must_also in CASES:
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "q.wav")
        tts(said, wav)
        t0 = time.time(); heard = transcribe(wav); stt = time.time() - t0
    t0 = time.time()
    r = suite.call([{"role": "user", "content": heard + "\n\nResponda de forma curta."}], think=False, max_tokens=200, stream=False)
    ans = norm(r.get("text") or "")
    ok = r["status"] == 200 and any(a in ans for a in must_any) and (not must_also or any(b in ans for b in must_also))
    rows.append({"spoken": said, "transcript": heard, "answer": (r.get("text") or "")[:200], "stt_s": round(stt, 2), "llm_s": round(time.time() - t0, 2), "pass": ok})
    print("PASS" if ok else "FAIL", "|", heard, "->", (r.get("text") or "")[:70].replace("\n", " "), flush=True)
os.makedirs("reports/ops", exist_ok=True)
json.dump({"cases": rows, "pass": all(r["pass"] for r in rows)}, open("reports/ops/pipeline-audio.json", "w"), indent=1, ensure_ascii=False)
