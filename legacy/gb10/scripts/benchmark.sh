#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:?usage: benchmark.sh https://api.example.com [api-key] [model]}"
API_KEY="${2:?missing API key}"
MODEL="${3:-qwen-abliterated}"
PROMPT="Explain why a binary search is O(log n), then give a short Python implementation."
payload=$(printf '{"model":"%s","temperature":0,"max_tokens":512,"messages":[{"role":"user","content":"%s"' "$MODEL" "$PROMPT")
payload+='}]}'

out=$(mktemp)
metrics=$(curl --fail --silent --show-error --max-time 300 -o "$out" \
  -w 'seconds=%{time_total} bytes=%{size_download}\n' \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $API_KEY" \
  --data "$payload" "$BASE_URL/v1/chat/completions")
python3 - "$out" "$metrics" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
usage = body.get("usage", {})
print(sys.argv[2])
print(json.dumps({"model": body.get("model"), "usage": usage}, indent=2))
if usage.get("completion_tokens") and "seconds=" in sys.argv[2]:
    seconds = float(sys.argv[2].split()[0].split("=", 1)[1])
    print(f"completion_tok_per_s={usage['completion_tokens'] / seconds:.2f}")
PY
rm -f "$out"

