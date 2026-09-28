#!/usr/bin/env python3
"""Long-context needle test: several prompt sizes x seeds. usage: BASE_URL/API_KEY env; python tests/longctx.py [sizes...]"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import suite
sizes = [int(x) for x in sys.argv[1:]] or [16000, 22000, 26000, 30000]
rows = []
for t in sizes:
    for seed in range(1, int(os.environ.get('SEEDS', '3')) + 1):
        needle = f"CODIGO-SECRETO-{40000 + t % 997 + seed}"
        hay = suite.filler(t, seed); pos = len(hay) // 2
        hay = hay[:pos] + f"\n\nA senha do cofre e {needle}.\n\n" + hay[pos:]
        r = suite.call([{"role": "user", "content": hay + "\n\nQual e a senha do cofre? Responda so com a senha."}], think=False, max_tokens=40)
        ok = needle in (r.get("text") or "")
        rows.append({"target": t, "seed": seed, "prompt_tokens": r.get("prompt_tokens"), "ttft": r.get("ttft"), "ok": ok, "head": (r.get("text") or "")[:40]})
        print(t, seed, r.get("prompt_tokens"), round(r.get("ttft") or 0, 1), "OK" if ok else "FAIL " + repr((r.get("text") or "")[:40]), flush=True)
json.dump(rows, open(os.environ.get("OUT", "reports/longctx/longctx.json"), "w"), indent=1)
