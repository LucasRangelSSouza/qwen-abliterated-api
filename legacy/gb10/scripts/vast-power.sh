#!/usr/bin/env bash
set -Eeuo pipefail
PY=python3; python3 -c '' 2>/dev/null || PY=python
# Programmatic on/off for the Vast instance (GPU billing stops when stopped).
# usage: VAST_API_KEY=... vast-power.sh <start|stop|status> [instance_id]
# The account-level key is required to START (a stopped container cannot hold a key).
ACTION="${1:?start|stop|status}"; ID="${2:-${VAST_INSTANCE_ID:?instance id}}"
: "${VAST_API_KEY:?set VAST_API_KEY (console.vast.ai -> Account -> API Keys)}"
API="https://console.vast.ai/api/v0/instances/$ID/"
auth=(-H "Authorization: Bearer $VAST_API_KEY" -H 'Content-Type: application/json')
case "$ACTION" in
  start)  curl -fsS -X PUT "${auth[@]}" -d '{"state":"running"}' "$API" ;;
  stop)   curl -fsS -X PUT "${auth[@]}" -d '{"state":"stopped"}' "$API" ;;
  status) curl -fsS "${auth[@]}" "$API" | $PY -c 'import json,sys
d=json.load(sys.stdin); i=d.get("instances",d)
print({k:i.get(k) for k in ("actual_status","cur_state","intended_status","dph_total","public_ipaddr","ssh_port","ports")})' ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
echo
