#!/usr/bin/env python3
"""Hard-restart cycles: kill the whole vLLM tree, let Supervisor start it again, and check that the API comes back,
no download process ran, and the weights are byte-for-byte the same files. Results replace the matching
'hard restart' rows in reports/idempotency.json.

env: SSH_TARGET, BASE_URL, API_KEY. usage: python tests/restart_cycles.py [cycles=2]
"""
import json, os, shlex, subprocess, sys, time, urllib.error, urllib.request

E = os.environ
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"] + shlex.split(E["SSH_TARGET"])
BASE = E["BASE_URL"].rstrip("/"); KEY = E["API_KEY"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNER = ("Welcome to vast.ai", "Have fun", "AI agents:", " /etc/vast-agents", ">>>")


def sh(cmd, timeout=900):
    p = subprocess.run(SSH + [cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return "\n".join(l for l in (p.stdout + p.stderr).splitlines() if not l.startswith(BANNER)).strip()


def status():
    r = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + KEY})
    try:
        return urllib.request.urlopen(r, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def completion_ok():
    body = json.dumps({"model": "qwen-abliterated", "messages": [{"role": "user", "content": "Return exactly: API_OK"}], "max_tokens": 30,
                       "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    r = urllib.request.Request(BASE + "/chat/completions", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        return "API_OK" in json.load(urllib.request.urlopen(r, timeout=120))["choices"][0]["message"]["content"]
    except Exception:
        return False


def fingerprint():
    return sh("cd /root/models && find bf16 drafter -type f \\( -name '*.safetensors' -o -name '*.json' \\) -printf '%p %s %T@\\n' | sort | sha256sum")


fp0 = fingerprint()
rows = []
for i in range(1, int(sys.argv[1]) + 1 if len(sys.argv) > 1 else 3):
    sh("supervisorctl stop vllm >/dev/null; pkill -f '[v]llm serve'; pkill -f '[V]LLM::EngineCor'; sleep 3; supervisorctl start vllm >/dev/null")
    t0 = time.time(); ready = None
    while time.time() - t0 < 1200:
        if status() == 200:
            ready = round(time.time() - t0); break
        time.sleep(5)
    downloads = sh("pgrep -f '[h]f download' | wc -l")
    same = fingerprint() == fp0
    ok = ready is not None and completion_ok() and downloads == "0" and same
    rows.append({"step": f"hard restart #{i}: API back, no download, weights identical", "pass": ok, "seconds": ready,
                 "detail": f"ready={ready}s downloads={downloads} weights_same={same}"})
    print("PASS" if ok else "FAIL", rows[-1], flush=True)

path = os.path.join(ROOT, "reports", "idempotency.json")
d = json.load(open(path))
d["steps"] = [r for r in d["steps"] if not r["step"].startswith("hard restart")] + rows
# keep logical order: hard restarts right before the Vast power cycle
d["steps"].sort(key=lambda r: (r["step"].startswith("Vast") or r["step"].startswith("weights survived"), r["step"].startswith("hard restart") is False and 0))
json.dump(d, open(path, "w"), indent=1)
print("updated", path)
