#!/usr/bin/env python3
"""Same quality benchmarks as tests/quality.py, but the model under test is Claude Code (`claude -p`) - the yardstick.

usage: python tests/quality_claude.py [--model claude-sonnet-5] [--effort medium] [--n-gsm 200] [--workers 4]
Identical prompts, identical local graders (HumanEval executed with timeouts, GSM8K exact match).
Tools are disabled and settings/CLAUDE.md are not loaded, so it is a plain single-shot completion.
Writes reports/quality-claude-<model>-<effort>.json and reports/claude-cost-<...>.json
"""
import argparse, concurrent.futures as cf, gzip, json, os, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("API_KEY", "unused")
import suite, quality  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="claude-sonnet-5"); ap.add_argument("--effort", default="medium")
ap.add_argument("--n-gsm", type=int, default=200); ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--n-he", type=int, default=164)
a = ap.parse_args()
TAG = f"{a.model}-{a.effort}"
NEUTRAL = tempfile.mkdtemp(prefix="claude-neutral-")
stats = {"calls": 0, "cost": 0.0, "out_tokens": 0, "api_ms": 0, "errors": 0}


def claude_call(messages, *, think=None, max_tokens=None, **_):
    prompt = "\n\n".join(m["content"] for m in messages if m["role"] == "user")
    cmd = ["claude", "-p", "--model", a.model, "--effort", a.effort, "--tools", "", "--no-session-persistence", "--disable-slash-commands",
           "--setting-sources", "", "--output-format", "json", "--system-prompt", "Answer directly and follow the requested output format exactly."]
    for attempt in range(3):
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, cwd=NEUTRAL)
            d = json.loads(p.stdout)
            if d.get("is_error"):
                raise RuntimeError(d.get("result", "")[:200])
            u = d.get("usage", {})
            stats["calls"] += 1; stats["cost"] += d.get("total_cost_usd", 0); stats["out_tokens"] += u.get("output_tokens", 0); stats["api_ms"] += d.get("duration_api_ms", 0)
            return {"status": 200, "text": d["result"], "tokens": u.get("output_tokens"), "ttft": None, "reasoning": ""}
        except Exception as e:  # retry transient failures
            err = repr(e)[:200]; time.sleep(3 * (attempt + 1))
    stats["errors"] += 1
    return {"status": 500, "error": err}


suite.call = claude_call
t0 = time.time()
he = [json.loads(l) for l in gzip.decompress(quality.fetch(quality.HE_URL, "HumanEval.jsonl.gz")).decode().splitlines()][:a.n_he]
gsm = [json.loads(l) for l in quality.fetch(quality.GSM_URL, "gsm8k_test.jsonl").decode().splitlines()][:a.n_gsm]
with cf.ThreadPoolExecutor(a.workers) as ex:
    he_res = list(ex.map(lambda p: quality.run_he(p, None), he))
    print(f"humaneval {sum(r['pass'] for r in he_res)}/{len(he_res)}  ({time.time() - t0:.0f}s)", flush=True)
    gsm_res = list(ex.map(lambda i: quality.run_gsm(i, None), gsm))
    print(f"gsm8k {sum(r['pass'] for r in gsm_res)}/{len(gsm_res)}  ({time.time() - t0:.0f}s)", flush=True)
os.makedirs("reports", exist_ok=True)
json.dump({"variant": TAG, "humaneval": {"passed": sum(r["pass"] for r in he_res), "total": len(he_res), "rows": he_res},
           "gsm8k": {"passed": sum(r["pass"] for r in gsm_res), "total": len(gsm_res), "rows": gsm_res}, "seconds": round(time.time() - t0), "usage": stats},
          open(f"reports/quality-{TAG}.json", "w"), indent=1)
print("saved", TAG, stats)
