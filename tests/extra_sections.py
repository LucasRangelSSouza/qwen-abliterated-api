"""Additional suite sections: vision, audio contract, parallel Q&A. Registered into suite.SECTIONS by suite.py."""
import base64
import concurrent.futures as cf
import struct
import time
import zlib


def _png(w, h, draw):
    """Tiny stdlib PNG writer: draw(x, y) -> (r, g, b)."""
    rows = []
    for y in range(h):
        rows.append(b"\x00" + b"".join(bytes(draw(x, y)) for x in range(w)))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make(call):
    def t_image():
        """Vision: synthetic image with a red square (left) and a blue circle (right) on white."""
        def px(x, y):
            if 20 <= x < 100 and 40 <= y < 120:
                return (220, 20, 20)
            if (x - 220) ** 2 + (y - 80) ** 2 < 45 ** 2:
                return (20, 40, 220)
            return (255, 255, 255)
        b64 = base64.b64encode(_png(300, 160, px)).decode()
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                   {"type": "text", "text": "Describe the image: which shapes and colours are there and where (left/right)? Answer in one sentence in English."}]
        r = call([{"role": "user", "content": content}], think=False, max_tokens=120)
        t = (r.get("text") or "").lower()
        ok = r["status"] == 200 and "red" in t and "blue" in t and ("square" in t or "rectangle" in t) and "circle" in t
        return {"status": r["status"], "answer": (r.get("text") or r.get("error") or "")[:240], "ttft": r.get("ttft"), "pass": ok}

    def t_audio():
        """Audio is not a modality of this vision-language model: the endpoint must refuse cleanly (4xx, no crash).
        The supported route is speech-to-text first, then this API (docs/RUNBOOK.md)."""
        pcm = b"\x00" * 3200
        wav = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
               + b"data" + struct.pack("<I", len(pcm)) + pcm)
        content = [{"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}},
                   {"type": "text", "text": "Transcribe."}]
        r = call([{"role": "user", "content": content}], think=False, max_tokens=50, stream=False)
        alive = call([{"role": "user", "content": "Return exactly: API_OK"}], think=False, max_tokens=20)
        return {"status": r["status"], "detail": (r.get("error") or r.get("text") or "")[:200],
                "server_still_ok": "API_OK" in (alive.get("text") or ""), "pass": r["status"] in (400, 422) and "API_OK" in (alive.get("text") or "")}

    def t_parallel_qa():
        """16 different questions with known answers sent at once: no cross-talk, every answer correct."""
        qs = [("Quanto e 12*12? Responda so o numero.", "144"), ("Capital da Franca? Responda so a cidade.", "paris"),
              ("Quanto e 250+175? So o numero.", "425"), ("Qual o simbolo quimico do ouro? So o simbolo.", "au"),
              ("Quantos dias tem uma semana? So o numero.", "7"), ("Raiz quadrada de 81? So o numero.", "9"),
              ("Capital do Japao? So a cidade.", "tokyo"), ("Quanto e 1000-999? So o numero.", "1"),
              ("Quantos lados tem um hexagono? So o numero.", "6"), ("Qual o maior planeta do sistema solar? So o nome.", "jupiter"),
              ("Quanto e 9*9? So o numero.", "81"), ("Capital da Italia? So a cidade.", "rom"),
              ("Quanto e 2 elevado a 10? So o numero.", "1024"), ("Qual oceano fica entre a America e a Europa? So o nome.", "atl"),
              ("Quanto e 100/4? So o numero.", "25"), ("Formula quimica da agua? So a formula.", "h2o")]

        def one(q):
            r = call([{"role": "user", "content": q[0]}], think=False, max_tokens=30)
            t = (r.get("text") or "").lower()
            return {"q": q[0], "ok": q[1] in t, "got": (r.get("text") or "").strip()[:30]}

        t0 = time.time()
        with cf.ThreadPoolExecutor(16) as ex:
            rows = list(ex.map(one, qs))
        return {"correct": sum(r["ok"] for r in rows), "of": len(rows), "wall": round(time.time() - t0, 1),
                "wrong": [r for r in rows if not r["ok"]], "pass": all(r["ok"] for r in rows)}

    return {"image": t_image, "audio": t_audio, "parallel_qa": t_parallel_qa}
