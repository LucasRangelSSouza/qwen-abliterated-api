#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:?usage: smoke-test.sh https://api.example.com [api-key] [model]}"
API_KEY="${2:?missing API key}"
MODEL="${3:-qwen-abliterated}"

models=$(curl --fail --silent --show-error --max-time 30 \
  -H "Authorization: Bearer $API_KEY" "$BASE_URL/v1/models")
printf '%s' "$models" | grep -Fq "\"$MODEL\""

payload=$(printf '{"model":"%s","temperature":0,"max_tokens":96,"messages":[{"role":"user","content":"Return exactly: API_OK"}]}' "$MODEL")
response=$(curl --fail --silent --show-error --max-time 180 \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $API_KEY" \
  --data "$payload" "$BASE_URL/v1/chat/completions")
printf '%s\n' "$response"
printf '%s' "$response" | grep -Fq 'API_OK'

