#!/usr/bin/env bash
set -Eeuo pipefail

# Native deployment path for Vast's maintained vLLM container template.
# It deliberately uses Supervisor rather than Docker-in-Docker.
#
# Profile: abliterated Qwen3.8-27B in NVFP4 (Blackwell FP4 tensor cores) plus the
# DFlash2 block-diffusion drafter. On GB10 (~273 GB/s) a dense 27B is memory-bound:
# BF16 ~4 tok/s, FP8 ~8, NVFP4 ~11.5, NVFP4+DFlash2 ~50 tok/s single stream.

TARGET_REPO="${TARGET_REPO:-Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4}"
DRAFTER_REPO="${DRAFTER_REPO:-z-lab/Qwen3.8-27B-DFlash2}"
MODELS_DIR="${MODELS_DIR:-/root/models}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen-abliterated}"
NSPEC="${NSPEC:-7}"
MAX_LEN="${MAX_LEN:-32768}"

command -v hf >/dev/null || { echo "Hugging Face CLI (hf) is required" >&2; exit 2; }
command -v supervisorctl >/dev/null || { echo "Vast vLLM image is required" >&2; exit 3; }

mkdir -p "$MODELS_DIR"
# Idempotent: hf download is a no-op when files are already complete, so a
# stop/start (or rerun) never re-downloads the weights.
hf download "$TARGET_REPO" --local-dir "$MODELS_DIR/target"
hf download "$DRAFTER_REPO" --local-dir "$MODELS_DIR/drafter"
test -s "$MODELS_DIR/target/model.safetensors.index.json"

# /workspace/.env is sourced by the image's Supervisor scripts.
ENV_FILE="${WORKSPACE:-/workspace}/.env"
touch "$ENV_FILE"
sed -i '/^VLLM_MODEL=/d;/^VLLM_ARGS=/d;/^MAX_JOBS=/d' "$ENV_FILE"
# The image's supervisor script eval()s VLLM_ARGS, so the JSON must be wrapped in
# single quotes inside a double-quoted env value.
SPEC="{\\\"method\\\":\\\"dflash\\\",\\\"model\\\":\\\"$MODELS_DIR/drafter\\\",\\\"num_speculative_tokens\\\":$NSPEC}"
{
  echo "MAX_JOBS=4"
  echo "VLLM_MODEL='$MODELS_DIR/target'"
  echo "VLLM_ARGS=\"--served-model-name $SERVED_MODEL_NAME --host 127.0.0.1 --port 18000 --gpu-memory-utilization 0.60 --max-model-len $MAX_LEN --max-num-seqs 4 --kv-cache-dtype fp8_e4m3 --trust-remote-code --enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3 --speculative-config '$SPEC'\""
} >>"$ENV_FILE"

supervisorctl stop vllm || true
# supervisor's stop leaves the vLLM API server/engine children holding :18000 and
# the GPU; kill them explicitly ("[x]" keeps pkill from matching this shell).
pkill -f "[v]llm serve" || true
pkill -f "[V]LLM::EngineCor" || true
for _ in $(seq 1 30); do ss -ltn | grep -q ':18000 ' || break; sleep 2; done
ss -ltn | grep -q ':18000 ' && { echo "port 18000 still busy" >&2; exit 4; }
supervisorctl start vllm
supervisorctl status vllm
