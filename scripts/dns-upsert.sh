#!/usr/bin/env bash
set -Eeuo pipefail
PY=python3; python3 -c '' 2>/dev/null || PY=python
# Idempotent A-record upsert through the Hostinger DNS API.
# usage: HOSTINGER_API_KEY=... dns-upsert.sh <domain> <name> <ipv4> [ttl]
# overwrite=false: only the named record is touched, the rest of the zone is preserved.
DOMAIN="${1:?domain}"; NAME="${2:?record name}"; IP="${3:?ipv4}"; TTL="${4:-300}"
: "${HOSTINGER_API_KEY:?set HOSTINGER_API_KEY}"
API="https://developers.hostinger.com/api/dns/v1/zones/$DOMAIN"

current=$(curl -fsS -H "Authorization: Bearer $HOSTINGER_API_KEY" "$API" |
  NAME="$NAME" $PY -c 'import json,os,sys
for r in json.load(sys.stdin):
    if r["name"]==os.environ["NAME"] and r["type"]=="A": print(r["records"][0]["content"])')
if [ "$current" = "$IP" ]; then echo "dns: $NAME.$DOMAIN already -> $IP"; exit 0; fi

body=$(NAME="$NAME" IP="$IP" TTL="$TTL" $PY -c 'import json,os
print(json.dumps({"overwrite":False,"zone":[{"name":os.environ["NAME"],"type":"A","ttl":int(os.environ["TTL"]),"records":[{"content":os.environ["IP"]}]}]}))')
curl -fsS -X PUT -H "Authorization: Bearer $HOSTINGER_API_KEY" -H 'Content-Type: application/json' -d "$body" "$API" >/dev/null
echo "dns: $NAME.$DOMAIN ${current:-<none>} -> $IP"
