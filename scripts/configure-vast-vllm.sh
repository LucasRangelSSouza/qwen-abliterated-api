#!/usr/bin/env bash
set -Eeuo pipefail

# Native deployment path for Vast's maintained vLLM container template.
# It deliberately uses Supervisor rather than Docker-in-Docker.

MODEL_REPO="${MODEL_REPO:-mradermacher/Qwen3.8-27B-OBLITERATED-GGUF}"
MODEL_FILE="${MODEL_FILE:-Qwen3.8-27B-OBLITERATED.Q4_K_M.gguf}"
TOKENIZER_REPO="${TOKENIZER_REPO:-OBLITERATUS/Qwen3.8-27B-OBLITERATED}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-/root/model-cache}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen-abliterated}"

command -v hf >/dev/null || { echo "Hugging Face CLI (hf) is required" >&2; exit 2; }
command -v supervisorctl >/dev/null || { echo "Vast vLLM image is required" >&2; exit 3; }

mkdir -p "$MODEL_CACHE_DIR"
hf download "$MODEL_REPO" "$MODEL_FILE" --local-dir "$MODEL_CACHE_DIR"

MODEL_PATH="$MODEL_CACHE_DIR/$MODEL_FILE"
test -s "$MODEL_PATH"

# /workspace/.env is sourced by the image's Supervisor scripts and is backed by
# a Vast local volume when one is attached. The large model stays on the
# configured instance disk so a small persistent volume is not exhausted.
ENV_FILE="${WORKSPACE:-/workspace}/.env"
touch "$ENV_FILE"
sed -i '/^VLLM_MODEL=/d;/^VLLM_ARGS=/d' "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF
VLLM_MODEL="$MODEL_PATH"
VLLM_ARGS="--load-format gguf --tokenizer $TOKENIZER_REPO --served-model-name $SERVED_MODEL_NAME --host 127.0.0.1 --port 18000 --gpu-memory-utilization 0.90 --max-model-len 16384 --max-num-seqs 4 --trust-remote-code --enable-reasoning --reasoning-parser qwen3"
EOF

supervisorctl restart vllm
supervisorctl status vllm
