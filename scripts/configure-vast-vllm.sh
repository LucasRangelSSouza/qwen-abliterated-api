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
# KV cache dtype: "auto" (bf16). fp8_e4m3 corrupted generations at >~20k tokens of context (tests/longctx.py:
# 12/36 needle failures with fp8, 0/12 with auto); memory is plentiful (270k tokens of KV in bf16).
KV_DTYPE="${KV_DTYPE:-auto}"
# Variants for experiments: TARGET_DIR (already-present weights, skips the target download), QUANT (e.g. fp8 = online
# quantisation of a BF16 checkpoint), GPU_UTIL (leave headroom for other GPU workloads).
TARGET_DIR="${TARGET_DIR:-}"
QUANT="${QUANT:-}"
GPU_UTIL="${GPU_UTIL:-0.60}"

command -v hf >/dev/null || { echo "Hugging Face CLI (hf) is required" >&2; exit 2; }
command -v supervisorctl >/dev/null || { echo "Vast vLLM image is required" >&2; exit 3; }

mkdir -p "$MODELS_DIR"
# Idempotent: hf download is a no-op when files are already complete, so a
# stop/start (or rerun) never re-downloads the weights.
if [ -z "$TARGET_DIR" ]; then
  hf download "$TARGET_REPO" --local-dir "$MODELS_DIR/target"
  TARGET_DIR="$MODELS_DIR/target"
fi
hf download "$DRAFTER_REPO" --local-dir "$MODELS_DIR/drafter"
test -s "$TARGET_DIR/model.safetensors.index.json"

# /workspace/.env is sourced by the image's Supervisor scripts.
ENV_FILE="${WORKSPACE:-/workspace}/.env"
touch "$ENV_FILE"
# Build the desired env block, compare with the current one and touch nothing when equal.
# The block is delimited by markers so unrelated lines in .env survive.
SPEC="{\\\"method\\\":\\\"dflash\\\",\\\"model\\\":\\\"$MODELS_DIR/drafter\\\",\\\"num_speculative_tokens\\\":$NSPEC}"
DESIRED=$(cat <<BLOCK
# BEGIN qwen-abliterated-api (managed)
MAX_JOBS=4
VLLM_MODEL='$TARGET_DIR'
VLLM_ARGS="--served-model-name $SERVED_MODEL_NAME --host 127.0.0.1 --port 18000 --gpu-memory-utilization $GPU_UTIL --max-model-len $MAX_LEN --max-num-seqs 4 --kv-cache-dtype $KV_DTYPE ${QUANT:+--quantization $QUANT} --trust-remote-code --enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3 --speculative-config '$SPEC'"
${VLLM_API_KEY:+VLLM_API_KEY='$VLLM_API_KEY'}
# END qwen-abliterated-api (managed)
BLOCK
)
CURRENT=$(sed -n '/^# BEGIN qwen-abliterated-api/,/^# END qwen-abliterated-api/p' "$ENV_FILE")
healthy() { curl -sf -m 5 http://127.0.0.1:18000/health >/dev/null; }
if [ "$CURRENT" = "$DESIRED" ] && supervisorctl status vllm | grep -q RUNNING && healthy; then
  echo "configure: env unchanged and vLLM healthy, nothing to do"
  exit 0
fi
# drop the managed block plus legacy unmanaged lines from earlier revisions
sed -i '/^# BEGIN qwen-abliterated-api/,/^# END qwen-abliterated-api/d;/^VLLM_MODEL=/d;/^VLLM_ARGS=/d;/^MAX_JOBS=/d;/^VLLM_API_KEY=/d' "$ENV_FILE"
printf '%s
' "$DESIRED" >>"$ENV_FILE"

supervisorctl stop vllm || true
# supervisor's stop leaves the vLLM API server/engine children holding :18000 and
# the GPU; kill them explicitly ("[x]" keeps pkill from matching this shell).
pkill -f "[v]llm serve" || true
pkill -f "[V]LLM::EngineCor" || true
for _ in $(seq 1 30); do ss -ltn | grep -q ':18000 ' || break; sleep 2; done
ss -ltn | grep -q ':18000 ' && { echo "port 18000 still busy" >&2; exit 4; }
supervisorctl start vllm
supervisorctl status vllm
