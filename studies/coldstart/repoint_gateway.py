"""Aponta o provedor `qq` do llm-gateway (tenant platform) para o llm-access em vez de direto para o llm-queue (Spec 14 §5.3).

Roda o Node DENTRO do container do gateway (o MT_ADMIN_TOKEN nunca sai dele). Idempotente. A chave PLATFORM vem do cofre local e
viaja so por stdin. Uso: python repoint_gateway.py [--rollback]   (--rollback volta para http://llm-queue.rt:8000/v1 e a chave do queue)
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

VAULT = Path(os.environ.get("VAULT_DIR", r"C:\Users\lucas.rangel\Desktop\claude\personal-skills\secrets"))


def env(name):
    d = {}
    for ln in (VAULT / name).read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            d[k] = v.split(" #")[0].strip()
    return d


rollback = "--rollback" in sys.argv
if rollback:
    keys = json.load(open(os.environ["LLMQ_KEYS_JSON"]))  # chave antiga do gateway no queue, guardada fora do repo
    base_url, api_key = "http://llm-queue.rt:8000/v1", keys["llm-gateway"]
else:
    base_url, api_key = "http://llm-access.rt:8000/v1", env("llm-access.env")["ACCESS_PLATFORM_KEY"]

NODE = r"""
const base = "http://127.0.0.1:20128";
const H = { "x-admin-token": process.env.MT_ADMIN_TOKEN, "content-type": "application/json" };
const T = { ...H, "x-tenant-id": input.tenant };
const call = async (method, path, headers, body) => {
  const r = await fetch(base + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let json; try { json = JSON.parse(text); } catch { json = { raw: text.slice(0, 300) }; }
  return { status: r.status, json };
};
const out = [];
let r = await call("GET", "/api/provider-nodes", T);
const nodes = r.json.nodes || r.json.providerNodes || (Array.isArray(r.json) ? r.json : []);
const node = nodes.find(n => n.prefix === input.prefix);
if (!node) { console.log(JSON.stringify({ erro: "no nao encontrado" })); return; }
r = await call("PUT", "/api/provider-nodes/" + node.id, T, { name: node.name, prefix: node.prefix, apiType: node.apiType || "chat", baseUrl: input.baseUrl });
out.push({ step: "node", status: r.status, antes: node.baseUrl, depois: input.baseUrl, erro: r.json.error });
r = await call("GET", "/api/providers", T);
const conns = r.json.connections || r.json.providers || (Array.isArray(r.json) ? r.json : []);
const conn = conns.find(c => c.provider === node.id);
if (conn) {
  r = await call("PUT", "/api/providers/" + conn.id, T, { apiKey: input.apiKey });
  out.push({ step: "conexao", status: r.status, erro: r.json.error });
} else out.push({ step: "conexao", erro: "nao encontrada" });
console.log(JSON.stringify(out));
"""
script = "(async()=>{const input=JSON.parse(require('fs').readFileSync(0,'utf8'));" + NODE + "})().catch(e=>{console.error(e);process.exit(1)})"
payload = json.dumps({"tenant": "platform", "prefix": "qq", "baseUrl": base_url, "apiKey": api_key})
b64 = base64.b64encode(script.encode()).decode()
remote = f'sudo -n docker exec -i -u 1000 llm-gateway node -e "$(echo {b64} | base64 -d)"'
r = subprocess.run(["ssh", "-i", os.environ.get("VPS_KEY", "/tmp/vk"), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "BatchMode=yes", "deploy@66.94.101.153", remote], input=payload, capture_output=True, text=True, timeout=300)
print("rc", r.returncode)
print((r.stdout.strip().splitlines() or [""])[-1][:600])
print(r.stderr[-300:])
