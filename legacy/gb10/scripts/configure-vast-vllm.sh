#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotent configuration of a Vast.ai vLLM container (Supervisor-managed, no Docker-in-Docker).
#
# Default profile "fp8": abliterated Qwen3.8-27B (BF16 checkpoint, quantised to FP8 by vLLM at load time)
# + the DFlash2 block-diffusion drafter, 160k context, bf16 KV cache.
#   PROFILE=nvfp4  -> pre-quantised Blackwell NVFP4 checkpoint instead (statistically the same quality, see docs/RESULTS.md,
#                     but slower prefill and lower aggregate throughput, so it is the alternative, not the default).
#
# Re-running it changes nothing when the desired state already holds: weights are only downloaded when missing,
# vLLM is only restarted when its managed config block differs or it is unhealthy.

PROFILE="${PROFILE:-fp8}"
case "$PROFILE" in
  fp8)
    TARGET_REPO="${TARGET_REPO:-Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16}"
    TARGET_SUBDIR="bf16"; QUANT="${QUANT:-fp8}"; OTHER_SUBDIR="target" ;;
  nvfp4)
    TARGET_REPO="${TARGET_REPO:-Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4}"
    TARGET_SUBDIR="target"; QUANT="${QUANT:-}"; OTHER_SUBDIR="bf16" ;;
  *) echo "unknown PROFILE=$PROFILE (fp8|nvfp4)" >&2; exit 2 ;;
esac
DRAFTER_REPO="${DRAFTER_REPO:-z-lab/Qwen3.8-27B-DFlash2}"
MODELS_DIR="${MODELS_DIR:-/root/models}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen-abliterated}"
NSPEC="${NSPEC:-7}"
MAX_LEN="${MAX_LEN:-160000}"
GPU_UTIL="${GPU_UTIL:-0.60}"            # leave headroom for other GPU workloads on the same card
# KV cache dtype: "auto" (bf16). fp8_e4m3 corrupted generations on long prompts (tests/longctx.py:
# needle tests failed with degenerate output with fp8, none with auto; see reports/longctx/); the KV cache holds ~400k tokens in bf16.
KV_DTYPE="${KV_DTYPE:-auto}"
PRUNE_UNUSED="${PRUNE_UNUSED:-0}"       # 1 = delete the other profile's weights (billed disk should not hold unused files)
TARGET_DIR="${TARGET_DIR:-$MODELS_DIR/$TARGET_SUBDIR}"

command -v hf >/dev/null || { echo "Hugging Face CLI (hf) is required" >&2; exit 2; }
command -v supervisorctl >/dev/null || { echo "Vast vLLM image is required" >&2; exit 3; }

# Speech-to-text sidecar: a second vLLM (openai/whisper-large-v3-turbo) on the same GPU serving
# /v1/audio/transcriptions with the same bearer key. WHISPER=0 disables it. It waits for the LLM to be healthy
# before starting, so the two servers never profile GPU memory at the same time.
WHISPER="${WHISPER:-1}"
WHISPER_REPO="${WHISPER_REPO:-openai/whisper-large-v3-turbo}"
WHISPER_PORT="${WHISPER_PORT:-3001}"      # internal port behind the mux
WHISPER_UTIL="${WHISPER_UTIL:-0.10}"

# Embedding sidecar: a third vLLM (pooling runner) serving /v1/embeddings. Vast only maps the ports declared at
# instance creation and 3000 is the only spare one, so a tiny path mux owns 3000 and fans out:
#   /v1/audio/* -> whisper (WHISPER_PORT), /v1/embeddings -> embed (EMBED_PORT). Backends enforce the bearer key.
# EMBED=0 disables it. Qwen3-Embedding is Matryoshka: request "dimensions":768 to be a drop-in for
# text-multilingual-embedding-002 (768-d).
EMBED="${EMBED:-1}"
EMBED_REPO="${EMBED_REPO:-Qwen/Qwen3-Embedding-4B}"
EMBED_NAME="${EMBED_NAME:-qwen-embedding}"
EMBED_PORT="${EMBED_PORT:-3002}"
EMBED_UTIL="${EMBED_UTIL:-0.12}"
EMBED_MAX_LEN="${EMBED_MAX_LEN:-8192}"
MUX_PORT="${MUX_PORT:-3000}"              # container port; Vast maps it to VAST_TCP_PORT_3000

setup_whisper() {
  [ "$WHISPER" = 1 ] || return 0
  hf download "$WHISPER_REPO" --local-dir "$MODELS_DIR/whisper" --include '*.json' --include '*.txt' --include 'model.safetensors' >/dev/null
  test -s "$MODELS_DIR/whisper/model.safetensors"
  local script=/opt/supervisor-scripts/whisper.sh conf=/etc/supervisor/conf.d/whisper.conf changed=0
  local want_script want_conf
  want_script=$(cat <<SCRIPT
#!/bin/bash
# managed by qwen-abliterated-api/configure-vast-vllm.sh
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate
until curl -sf http://127.0.0.1:18000/health >/dev/null; do sleep 5; done
exec vllm serve $MODELS_DIR/whisper --served-model-name whisper --host 0.0.0.0 --port $WHISPER_PORT \\
  --gpu-memory-utilization $WHISPER_UTIL --max-model-len 448 --max-num-seqs 8 --api-key "\${VLLM_API_KEY:-}" 2>&1
SCRIPT
)
  want_conf=$(cat <<CONF
[program:whisper]
environment=PROC_NAME="%(program_name)s"
command=$script
autostart=true
autorestart=unexpected
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_logfile_maxbytes=0
CONF
)
  [ "$(cat "$script" 2>/dev/null)" = "$want_script" ] || { printf '%s\n' "$want_script" >"$script"; chmod +x "$script"; changed=1; }
  [ "$(cat "$conf" 2>/dev/null)" = "$want_conf" ] || { printf '%s\n' "$want_conf" >"$conf"; changed=1; }
  if [ "$changed" = 1 ]; then
    supervisorctl reread >/dev/null && supervisorctl update >/dev/null
    supervisorctl restart whisper >/dev/null || true
    echo "whisper: service (re)configured"
  else
    supervisorctl status whisper | grep -q RUNNING || supervisorctl start whisper >/dev/null || true
    echo "whisper: service unchanged"
  fi
}

# install_service NAME SCRIPT_BODY: write Supervisor program + script, restart only when either changed.
install_service() {
  local name="$1" body="$2" script="/opt/supervisor-scripts/$1.sh" conf="/etc/supervisor/conf.d/$1.conf" changed=0 want_conf
  want_conf=$(cat <<CONF
[program:$name]
environment=PROC_NAME="%(program_name)s"
command=$script
autostart=true
autorestart=unexpected
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_logfile_maxbytes=0
CONF
)
  [ "$(cat "$script" 2>/dev/null)" = "$body" ] || { printf '%s\n' "$body" >"$script"; chmod +x "$script"; changed=1; }
  [ "$(cat "$conf" 2>/dev/null)" = "$want_conf" ] || { printf '%s\n' "$want_conf" >"$conf"; changed=1; }
  if [ "$changed" = 1 ]; then
    supervisorctl reread >/dev/null && supervisorctl update >/dev/null
    supervisorctl restart "$name" >/dev/null || true
    echo "$name: service (re)configured"
  else
    supervisorctl status "$name" | grep -q RUNNING || supervisorctl start "$name" >/dev/null || true
    echo "$name: service unchanged"
  fi
}

setup_embed() {
  [ "$EMBED" = 1 ] || return 0
  hf download "$EMBED_REPO" --local-dir "$MODELS_DIR/embed" >/dev/null
  test -s "$MODELS_DIR/embed/config.json"
  install_service embed "$(cat <<SCRIPT
#!/bin/bash
# managed by qwen-abliterated-api/configure-vast-vllm.sh
utils=/opt/supervisor-scripts/utils
. "\${utils}/logging.sh"
. "\${utils}/environment.sh"
[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate
until curl -sf http://127.0.0.1:18000/health >/dev/null; do sleep 5; done
# older vLLM spells the pooling runner --task embed
if vllm serve --help=all 2>/dev/null | grep -q -- '--runner'; then RUN="--runner pooling --convert embed"; else RUN="--task embed"; fi
exec vllm serve $MODELS_DIR/embed \$RUN --served-model-name $EMBED_NAME --host 127.0.0.1 --port $EMBED_PORT \\
  --gpu-memory-utilization $EMBED_UTIL --max-model-len $EMBED_MAX_LEN --max-num-seqs 64 --max-num-batched-tokens 32768 \\
  --hf-overrides '{"is_matryoshka":true}' --api-key "\${VLLM_API_KEY:-}" 2>&1
SCRIPT
)"
}

setup_mux() {
  [ "$WHISPER" = 1 ] || [ "$EMBED" = 1 ] || return 0
  local py=/opt/qwen-mux.py
  local want_py
  want_py=$(cat <<PY
# managed by qwen-abliterated-api/configure-vast-vllm.sh: path mux on the single spare Vast port
import httpx, uvicorn
from fastapi import FastAPI, Request, Response
ROUTES = {"/v1/audio": "http://127.0.0.1:$WHISPER_PORT", "/v1/embeddings": "http://127.0.0.1:$EMBED_PORT"}
app = FastAPI()
client = httpx.AsyncClient(timeout=600)
@app.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request):
    full = "/" + path
    base = next((b for k, b in ROUTES.items() if full.startswith(k)), None)
    if base is None:
        return Response(status_code=404)
    r = await client.request(request.method, base + full, content=await request.body(),
                             headers={k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type")})
    return Response(r.content, r.status_code, media_type=r.headers.get("content-type"))
uvicorn.run(app, host="0.0.0.0", port=$MUX_PORT, log_level="warning")
PY
)
  [ "$(cat "$py" 2>/dev/null)" = "$want_py" ] || printf '%s\n' "$want_py" >"$py"
  install_service mux "$(cat <<SCRIPT
#!/bin/bash
# managed by qwen-abliterated-api/configure-vast-vllm.sh
[[ -f /venv/main/bin/activate ]] && . /venv/main/bin/activate
exec python $py 2>&1
SCRIPT
)"
}

setup_sidecars() { setup_whisper; setup_embed; setup_mux; }

mkdir -p "$MODELS_DIR"
# hf download is a no-op when the files are already complete, so stop/start and re-runs never re-download.
hf download "$TARGET_REPO" --local-dir "$TARGET_DIR"
hf download "$DRAFTER_REPO" --local-dir "$MODELS_DIR/drafter"
test -s "$TARGET_DIR/model.safetensors.index.json"
if [ "$PRUNE_UNUSED" = 1 ] && [ -d "$MODELS_DIR/$OTHER_SUBDIR" ]; then
  echo "prune: removing unused $MODELS_DIR/$OTHER_SUBDIR"; rm -rf "$MODELS_DIR/$OTHER_SUBDIR"
fi

# /workspace/.env is sourced by the image's Supervisor scripts. The managed block is delimited by markers so
# unrelated lines survive; it is compared with the desired block to decide whether anything must change.
ENV_FILE="${WORKSPACE:-/workspace}/.env"
touch "$ENV_FILE"
# The supervisor script eval()s VLLM_ARGS, so the JSON needs single quotes inside a double-quoted env value.
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
if [ "$CURRENT" = "$DESIRED" ] && supervisorctl status vllm | grep -q RUNNING; then
  # Right config and the process is up: it may simply still be loading weights (minutes). Give it time
  # instead of restarting it mid-load; only a server that never becomes healthy is restarted below.
  for _ in $(seq 1 ${HEALTH_WAIT_S:-900}); do
    if healthy; then setup_sidecars; echo "configure: env unchanged and vLLM healthy, nothing to do"; exit 0; fi
    supervisorctl status vllm | grep -q RUNNING || break
    sleep 1
  done
  echo "configure: vLLM did not become healthy, restarting"
fi
# drop the managed block plus legacy unmanaged lines from earlier revisions
sed -i '/^# BEGIN qwen-abliterated-api/,/^# END qwen-abliterated-api/d;/^VLLM_MODEL=/d;/^VLLM_ARGS=/d;/^MAX_JOBS=/d;/^VLLM_API_KEY=/d' "$ENV_FILE"
printf '%s\n' "$DESIRED" >>"$ENV_FILE"

supervisorctl stop vllm || true
# supervisor's stop leaves the vLLM API server/engine children holding :18000 and the GPU;
# kill them explicitly ("[x]" keeps pkill from matching this very shell).
pkill -f "[v]llm serve" || true
pkill -f "[V]LLM::EngineCor" || true
for _ in $(seq 1 30); do ss -ltn | grep -q ':18000 ' || break; sleep 2; done
ss -ltn | grep -q ':18000 ' && { echo "port 18000 still busy" >&2; exit 4; }
supervisorctl start vllm
supervisorctl status vllm
setup_sidecars
