#!/usr/bin/env bash
set -Eeuo pipefail
# Publish the Vast-hosted vLLM behind https://<host> on the Traefik edge VPS.
# Traefik terminates TLS (Let's Encrypt) and forwards to the Vast mapped port,
# injecting the Vast edge auth cookie so clients only need the stable VLLM_API_KEY.
# Re-run whenever the Vast IP/port/token changes (recreate); it is idempotent.
#
# env: EDGE_SSH ("root@66.94.101.153"), EDGE_KEY (ssh key), PUBLIC_HOST (qwen.rangeltech.net),
#      VAST_IP, VAST_PORT (mapped 8000), VAST_LABEL (C.<id>), VAST_TOKEN
: "${EDGE_SSH:?}" "${PUBLIC_HOST:?}" "${VAST_IP:?}" "${VAST_PORT:?}" "${VAST_LABEL:?}" "${VAST_TOKEN:?}"
EDGE_KEY="${EDGE_KEY:-$HOME/.ssh/id_ed25519}"
DYN="${TRAEFIK_DYNAMIC:-/opt/platform/configs/traefik/dynamic.yml}"

ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i "$EDGE_KEY" "$EDGE_SSH" \
  "PUBLIC_HOST='$PUBLIC_HOST' VAST_IP='$VAST_IP' VAST_PORT='$VAST_PORT' VAST_LABEL='$VAST_LABEL' VAST_TOKEN='$VAST_TOKEN' DYN='$DYN' python3 -" <<'PY'
import os, re, shutil, time
e = os.environ
p = e["DYN"]
s = open(p).read()
router = f'''    qwen-vast-router:  # managed by qwen-abliterated-api/publish-endpoint.sh
      rule: "Host(`{e['PUBLIC_HOST']}`)"
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      priority: 100
      service: qwen-vast
      middlewares:
        - qwen-vast-auth
        - security-headers
'''
mw = f'''    qwen-vast-auth:  # managed by qwen-abliterated-api/publish-endpoint.sh
      headers:
        customRequestHeaders:
          Cookie: "{e['VAST_LABEL']}_auth_token={e['VAST_TOKEN']}"
'''
svc = f'''    qwen-vast:  # managed by qwen-abliterated-api/publish-endpoint.sh
      loadBalancer:
        passHostHeader: false
        responseForwarding:
          flushInterval: 1ms
        servers:
          - url: "http://{e['VAST_IP']}:{e['VAST_PORT']}"
'''
def upsert(s, section, name, block):
    # remove previous managed block for this key, then append at end of section
    pat = re.compile(rf"^    {name}:  # managed by qwen-abliterated-api.*?(?=^    \S|^  \S|^\S|\Z)", re.S | re.M)
    s = pat.sub("", s)
    m = re.search(rf"^  {section}:\n", s, re.M)
    assert m, section
    # end of section = next line at indent<=2 after m.end()
    n = re.search(r"^(?:  \S|\S)", s[m.end():], re.M)
    end = m.end() + (n.start() if n else len(s) - m.end())
    return s[:end] + block + s[end:]
new = upsert(upsert(upsert(s, "routers", "qwen-vast-router", router), "middlewares", "qwen-vast-auth", mw), "services", "qwen-vast", svc)
if new == s:
    print("edge: dynamic.yml unchanged")
else:
    shutil.copy(p, f"{p}.bak-qwen-{int(time.time())}")
    open(p, "w").write(new)   # in place: keeps the bind-mount inode
    print("edge: dynamic.yml updated")
PY
