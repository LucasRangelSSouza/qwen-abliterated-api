#!/usr/bin/env python3
"""Idempotency / restart tests. Drives the instance over SSH and the public URL over HTTPS.

env: SSH_TARGET ("-i KEY -p PORT root@HOST"), BASE_URL, API_KEY, HOSTINGER_API_KEY,
     EDGE_SSH, EDGE_KEY, VAST_IP, VAST_PORT, VAST_LABEL, VAST_TOKEN, (optional) VAST_API_KEY + VAST_INSTANCE_ID
Writes reports/idempotency.json.
"""
import json, os, shlex, subprocess, sys, time, urllib.request, urllib.error

E = os.environ
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"] + shlex.split(E["SSH_TARGET"])
BASE = E["BASE_URL"].rstrip("/"); KEY = E["API_KEY"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = next((p for p in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe") if os.path.exists(p)), "bash")
steps = []


def sh(cmd, timeout=900):
    p = subprocess.run(SSH + [cmd], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    text = p.stdout + p.stderr
    keep = [l for l in text.splitlines() if not l.startswith(("Welcome to vast.ai", "Have fun", "AI agents:", " /etc/vast-agents", ">>>"))]
    return p.returncode, "
".join(keep).strip()


def local(cmd, env=None, timeout=300):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env={**os.environ, **(env or {})}, shell=isinstance(cmd, str))
    return p.returncode, (p.stdout + p.stderr).strip()


def public_status():
    r = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + KEY})
    try:
        return urllib.request.urlopen(r, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def wait_public(limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        if public_status() == 200:
            return round(time.time() - t0)
        time.sleep(5)
    return None


def completion_ok():
    body = json.dumps({"model": "qwen-abliterated", "messages": [{"role": "user", "content": "Return exactly: API_OK"}], "max_tokens": 30,
                       "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    r = urllib.request.Request(BASE + "/chat/completions", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        return "API_OK" in json.load(urllib.request.urlopen(r, timeout=120))["choices"][0]["message"]["content"]
    except Exception:
        return False


def rec(step, ok, seconds=None, detail=""):
    steps.append({"step": step, "pass": bool(ok), "seconds": seconds, "detail": detail})
    print(("PASS" if ok else "FAIL"), step, seconds, detail, flush=True)


def weights_fingerprint():
    return sh("cd /root/models && find bf16 drafter -type f \\( -name '*.safetensors' -o -name '*.json' \\) -printf '%p %s %T@\\n' | sort | sha256sum")[1]


def vllm_pid():
    return sh("pgrep -f '[v]llm serve' | head -1")[1]


# 0. baseline
assert public_status() == 200, "endpoint must be up before idempotency tests"
fp0 = weights_fingerprint(); pid0 = vllm_pid()
rec("baseline endpoint up + completion", completion_ok(), detail=f"pid={pid0}")

# 1. configure re-run when nothing changed: no download, no restart
with open(os.path.join(ROOT, "scripts", "configure-vast-vllm.sh"), "rb") as fh:
    src = fh.read().replace(b"\r\n", b"\n")
p = subprocess.run(SSH + ["cat > /root/configure.sh && chmod +x /root/configure.sh"], input=src, capture_output=True)
t0 = time.time(); rc, out = sh(f"VLLM_API_KEY='{KEY}' /root/configure.sh"); dt = round(time.time() - t0)
pid1 = vllm_pid(); fp_same = weights_fingerprint() == fp0
rec("configure.sh no-op when config unchanged", rc == 0 and "nothing to do" in out and pid1 == pid0 and fp_same, dt,
    f"rc={rc} nothing_to_do={'nothing to do' in out} pid {pid0}->{pid1} weights_same={fp_same}")

# 2. hf download re-run is a no-op (no bytes moved)
t0 = time.time(); rc, out = sh("hf download Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16 --local-dir /root/models/bf16 >/dev/null 2>&1; echo rc=$?"); dt = round(time.time() - t0)
rec("hf download re-run moves no bytes", "rc=0" in out and weights_fingerprint() == fp0 and dt < 60, dt)

# 3. publish-endpoint twice: second is unchanged
env = {"EDGE_SSH": E["EDGE_SSH"], "EDGE_KEY": E["EDGE_KEY"], "PUBLIC_HOST": E.get("PUBLIC_HOST", "qwen.rangeltech.net"), "VAST_IP": E["VAST_IP"],
       "VAST_PORT": E["VAST_PORT"], "VAST_LABEL": E["VAST_LABEL"], "VAST_TOKEN": E["VAST_TOKEN"]}
local([BASH, os.path.join(ROOT, "scripts", "publish-endpoint.sh")], env)
rc, out = local([BASH, os.path.join(ROOT, "scripts", "publish-endpoint.sh")], env)
rec("publish-endpoint.sh second run unchanged", rc == 0 and "unchanged" in out, detail=out.splitlines()[-1] if out else "")
n = subprocess.run(["ssh", "-o", "BatchMode=yes", "-i", E["EDGE_KEY"], E["EDGE_SSH"], "grep -c 'managed by qwen-abliterated-api' /opt/platform/configs/traefik/dynamic.yml"], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout.strip()
rec("publish-endpoint.sh leaves exactly 3 managed blocks (no duplication)", n == "3", detail=f"blocks={n}")

# 4. dns-upsert twice
rc, out = local([BASH, os.path.join(ROOT, "scripts", "dns-upsert.sh"), "rangeltech.net", "qwen", "66.94.101.153"], {"HOSTINGER_API_KEY": E["HOSTINGER_API_KEY"]})
rec("dns-upsert.sh already in place", rc == 0 and "already" in out, detail=out[-80:])

# 5. drift repair: corrupt the managed block, configure must repair and end healthy
sh("sed -i 's/--max-model-len 160000/--max-model-len 4096/' /workspace/.env")
t0 = time.time(); rc, out = sh(f"VLLM_API_KEY='{KEY}' /root/configure.sh >/tmp/configure.log 2>&1; echo rc=$?"); ready = wait_public(900)
rec("configure.sh repairs config drift and API returns", "rc=0" in out and ready is not None and completion_ok(), round(time.time() - t0),
    f"max_model_len restored={'160000' in sh('grep -o max-model-len.[0-9]* /workspace/.env')[1]}")
fp1 = weights_fingerprint()
rec("weights untouched by drift repair (no re-download)", fp1 == fp0)

ONLY = E.get("ONLY", "")
# 6. forced restart cycles: hard kill of the whole vLLM tree, supervisor start, no download
for i in (1, 2):
    sh("supervisorctl stop vllm >/dev/null; pkill -f '[v]llm serve'; pkill -f '[V]LLM::EngineCor'; sleep 3; supervisorctl start vllm >/dev/null")
    t0 = time.time(); ready = wait_public(900); dt = round(time.time() - t0)
    dl = sh("pgrep -f '[h]f download' | wc -l")[1]
    rec(f"hard restart #{i}: API back, no download, weights identical", ready is not None and completion_ok() and dl == "0" and weights_fingerprint() == fp0, dt)

# 7. optional real power cycle through the Vast API
if E.get("VAST_API_KEY") and E.get("VAST_INSTANCE_ID"):
    vp = os.path.join(ROOT, "scripts", "vast-power.sh")
    penv = {"VAST_API_KEY": E["VAST_API_KEY"], "VAST_INSTANCE_ID": E["VAST_INSTANCE_ID"]}
    t0 = time.time(); rc, out = local([BASH, vp, "stop"], penv)
    time.sleep(45); down = public_status() != 200
    rec("Vast stop via API: endpoint goes down", rc == 0 and down, round(time.time() - t0), out[-100:])
    t0 = time.time(); rc, out = local([BASH, vp, "start"], penv)
    up = wait_public(600)
    if up is None:  # Vast may hand back a different IP/port set: re-publish the route (idempotent) and try again
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"https://console.vast.ai/api/v0/instances/{E['VAST_INSTANCE_ID']}/",
                         headers={"Authorization": "Bearer " + E["VAST_API_KEY"]}), timeout=30))
        i = info.get("instances", info)
        ports = i.get("ports", {})
        new_api = ports.get("8000/tcp", [{}])[0].get("HostPort")
        rec("Vast start: endpoint needed re-publish (IP/port changed)", True, detail=f"ip={i.get('public_ipaddr')} api_port={new_api}")
        if new_api:
            env2 = {**env, "VAST_IP": i["public_ipaddr"], "VAST_PORT": str(new_api)}
            local([BASH, os.path.join(ROOT, "scripts", "publish-endpoint.sh")], env2)
        up = wait_public(900)
    rec("Vast start via API: same endpoint returns without redeploy", rc == 0 and up is not None and completion_ok(), round(time.time() - t0), out[-100:])
    rec("weights survived stop/start", weights_fingerprint() == fp0)
else:
    rec("Vast stop/start via API", False, detail="SKIPPED: VAST_API_KEY not provided")

os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
json.dump({"steps": steps}, open(os.path.join(ROOT, "reports", "idempotency.json"), "w"), indent=1)
print("saved reports/idempotency.json")
