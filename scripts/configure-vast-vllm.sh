#!/usr/bin/env bash
set -Eeuo pipefail

# Native deployment path for Vast's maintained vLLM container template.
# It deliberately uses Supervisor rather than Docker-in-Docker.

MODEL_REPO="${MODEL_REPO:-OBLITERATUS/Qwen3.8-27B-OBLITERATED}"
TOKENIZER_REPO="${TOKENIZER_REPO:-$MODEL_REPO}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-/root/model-cache}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen-abliterated}"

command -v hf >/dev/null || { echo "Hugging Face CLI (hf) is required" >&2; exit 2; }
command -v supervisorctl >/dev/null || { echo "Vast vLLM image is required" >&2; exit 3; }

mkdir -p "$MODEL_CACHE_DIR"
# Vast's bundled vLLM 0.30 rejects GGUF at the model loader. Download only the
# native BF16 shards and their metadata; do not clone unrelated GGUF variants.
hf download "$MODEL_REPO" \
  --include 'model-*-of-00028.safetensors' \
  --include 'model-extra-*.safetensors' \
  --include 'model.safetensors.index.json' \
  --include 'config.json' \
  --include 'generation_config.json' \
  --include 'tokenizer.json' \
  --include 'tokenizer_config.json' \
  --include 'vocab.json' \
  --include 'merges.txt' \
  --include 'chat_template.jinja' \
  --include 'preprocessor_config.json' \
  --include 'video_preprocessor_config.json' \
  --local-dir "$MODEL_CACHE_DIR"

test -s "$MODEL_CACHE_DIR/model.safetensors.index.json"

# /workspace/.env is sourced by the image's Supervisor scripts and is backed by
# a Vast local volume when one is attached. The large model stays on the
# configured instance disk so a small persistent volume is not exhausted.
ENV_FILE="${WORKSPACE:-/workspace}/.env"
touch "$ENV_FILE"
sed -i '/^VLLM_MODEL=/d;/^VLLM_ARGS=/d' "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF
VLLM_MODEL="$MODEL_CACHE_DIR"
VLLM_ARGS="--tokenizer $TOKENIZER_REPO --served-model-name $SERVED_MODEL_NAME --host 127.0.0.1 --port 18000 --gpu-memory-utilization 0.90 --max-model-len 16384 --max-num-seqs 4 --trust-remote-code --reasoning-parser qwen3"
EOF

supervisorctl restart vllm
supervisorctl status vllm
