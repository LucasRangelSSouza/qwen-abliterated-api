#!/usr/bin/env python3
"""Sequential experiment queue: deploy a variant on the instance, run the same benchmarks, keep raw JSON per variant.

env: SSH_TARGET, BASE_URL, API_KEY (see tests/idempotency.py). Runs unattended; progress in reports/ops/queue.log.
Variants are dicts of configure-vast-vllm.sh env vars.
"""
import json, os, shlex, subprocess, sys, time, urllib.request, urllib.error

E = os.environ
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"] + shlex.split(E["SSH_TARGET"])
BASE = E["BASE_URL"].rstrip("/"); KEY = E["API_KEY"]
LOG = open(os.path.join(ROOT, "reports", "ops", "queue.log"), "a", buffering=1)


def log(*a):
    m = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(m, flush=True); LOG.write(m + "\n")


def sh(cmd, timeout=1800, inp=None):
    p = subprocess.run(SSH + [cmd], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, input=inp)
    return p.returncode, (p.stdout + p.stderr).strip()


def status():
    r = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + KEY})
    try:
        return urllib.request.urlopen(r, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def wait_ready(limit=1500):
    t0 = time.time()
    while time.time() - t0 < limit:
        if status() == 200:
            return round(time.time() - t0)
        time.sleep(5)
    return None


def deploy(name, env):
    log(f"== deploy {name} {env}")
    src = open(os.path.join(ROOT, "scripts", "configure-vast-vllm.sh"), "rb").read().replace(b"\r\n", b"\n")
    subprocess.run(SSH + ["cat > /root/configure.sh && chmod +x /root/configure.sh"], input=src, capture_output=True)
    envs = " ".join(f"{k}='{v}'" for k, v in {**env, "VLLM_API_KEY": KEY}.items())
    t0 = time.time()
    rc, out = sh(f"{envs} /root/configure.sh >/tmp/configure.log 2>&1; echo rc=$?")
    if "rc=0" not in out:
        log(f"!! configure failed for {name}: {out[-300:]}")
        return False
    ready = wait_ready()
    facts = sh("grep -E 'GPU KV cache size|Maximum concurrency|Model loading took|Available KV' /var/log/vllm.log | tail -4 | cut -c1-220")[1]
    log(f"deploy {name}: {out.splitlines()[-1] if out else ''} ready_after={ready}s total={round(time.time()-t0)}s\n{facts}")
    return ready is not None


def run(cmd, env=None):
    log("run", " ".join(cmd))
    p = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1", **(env or {})}, capture_output=True, text=True, encoding='utf-8', errors='replace')
    log((p.stdout + p.stderr)[-1500:])
    return p.returncode == 0


def variant(name, env, sizes, seeds="2", full_suite=True):
    if not deploy(name, env):
        log(f"!! {name} did not become ready, skipping"); return
    if full_suite:
        run([sys.executable, "-u", "tests/suite.py", "--out", f"reports/runs/run-{name}.json"])
    run([sys.executable, "-u", "tests/longctx.py", *[str(s) for s in sizes]], {"OUT": f"reports/longctx/longctx-{name}.json", "SEEDS": seeds})


if __name__ == "__main__":
    # wait for the BF16 download that FP8 online quantisation starts from
    while "BF16DONE" not in sh("cat /workspace/models/bf16.log 2>/dev/null | tail -c 300")[1]:
        time.sleep(20)
    log("bf16 present")
    LONG = [32000, 64000, 100000, 150000]
    variant("fp8-160k", {"TARGET_DIR": "/workspace/models/bf16", "QUANT": "fp8", "MAX_LEN": "160000"}, LONG)
    variant("nvfp4-160k", {"MAX_LEN": "160000"}, LONG, full_suite=False)
    log("QUEUE DONE")
