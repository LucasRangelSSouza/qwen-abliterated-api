#!/usr/bin/env python3
"""One entry point: full endpoint suite, audio pipeline, idempotency (incl. real Vast stop/start when VAST_API_KEY is set),
then the Markdown reports. Everything lands under reports/ (see reports/README.md).

env: BASE_URL, API_KEY (always); for idempotency also SSH_TARGET, HOSTINGER_API_KEY, EDGE_SSH, EDGE_KEY, VAST_IP, VAST_PORT,
     VAST_LABEL, VAST_TOKEN, WHISPER_PORT, VAST_API_KEY, VAST_INSTANCE_ID.
usage: python tests/run_all.py [--tag final-fp8] [--skip idempotency,pipeline]
"""
import argparse, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser(); ap.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M")); ap.add_argument("--skip", default="")
a = ap.parse_args()
skip = set(a.skip.split(","))
py = [sys.executable, "-u"]
env = {**os.environ, "PYTHONUTF8": "1"}


def step(name, cmd, out=None):
    if name in skip:
        print(f"== skip {name}"); return True
    print(f"== {name}: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT, env=env)
    print(f"== {name} finished rc={p.returncode} in {time.time() - t0:.0f}s", flush=True)
    return p.returncode == 0


run_json = f"reports/runs/run-{a.tag}.json"
step("suite", py + ["tests/suite.py", "--out", run_json])
step("pipeline", py + ["tests/pipeline_audio.py"])
step("idempotency", py + ["tests/idempotency.py"])
idem = "reports/ops/idempotency.json" if os.path.exists(os.path.join(ROOT, "reports/ops/idempotency.json")) and "idempotency" not in skip else None
with open(os.path.join(ROOT, f"reports/runs/REPORT-{a.tag}.md"), "w", encoding="utf-8") as f:
    subprocess.run([sys.executable, "tests/make_report.py", run_json] + ([idem] if idem else []), cwd=ROOT, env=env, stdout=f)
step("results", [sys.executable, "-c", "print()"])  # placeholder so the log shows a closing line
print(f"reports written: {run_json}, reports/runs/REPORT-{a.tag}.md")
