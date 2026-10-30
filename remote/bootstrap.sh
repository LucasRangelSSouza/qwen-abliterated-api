#!/bin/bash
# onstart do llm-queue na instancia da Vast (Spec 13 §4 e §5.2). Idempotente: roda em TODA partida do contêiner (criar ou religar).
# Variaveis (vindas do `env` da criacao): LLM_REPO, EMBED_REPO, EMBED_MODE (off|gpu), LLM_UTIL, EMBED_UTIL, WATCHDOG_IDLE_MIN.
set -u
mkdir -p /workspace/models
exec >> /workspace/llmq-bootstrap.log 2>&1
echo "== bootstrap $(date -u +%FT%TZ)"
mkdir -p /workspace/models
# shellcheck disable=SC1091
[ -f /venv/main/bin/activate ] && . /venv/main/bin/activate

LLM_REPO=${LLM_REPO:-twolven/Qwen3.8-27B-abliterated-AWQ-MTP}
EMBED_REPO=${EMBED_REPO:-Qwen/Qwen3-Embedding-4B}
EMBED_MODE=${EMBED_MODE:-off}
LLM_UTIL=${LLM_UTIL:-0.92}
EMBED_UTIL=${EMBED_UTIL:-0.24}
LLM_DIR=/workspace/models/awq
EMBED_DIR=/workspace/models/embed

pip install -q hf_transfer >/dev/null 2>&1 && export HF_HUB_ENABLE_HF_TRANSFER=1

dl() {  # $1=repo $2=dir ; no-op se completo; tenta de novo sem hf_transfer se falhar
  hf download "$1" --local-dir "$2" >/dev/null 2>&1 || { unset HF_HUB_ENABLE_HF_TRANSFER; hf download "$1" --local-dir "$2" >/dev/null 2>&1; }
  test -s "$2/config.json"
}

dl "$LLM_REPO" "$LLM_DIR" || { echo "FALHA: download da LLM"; exit 1; }
if [ "$EMBED_MODE" = gpu ]; then dl "$EMBED_REPO" "$EMBED_DIR" || echo "AVISO: download do embedding falhou"; fi

healthy() { [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/health")" = 200 ]; }

# LLM (so escuta em 127.0.0.1: o acesso e por tunel SSH aberto pelo llm-queue)
if ! healthy 18000 && ! pgrep -f "port 18000" >/dev/null; then
  nohup vllm serve "$LLM_DIR" --served-model-name qwen-abliterated --host 127.0.0.1 --port 18000 \
    --tensor-parallel-size 2 --gpu-memory-utilization "$LLM_UTIL" --max-model-len 65536 --max-num-seqs 24 \
    --max-num-batched-tokens 16384 --reasoning-parser qwen3 --enable-prefix-caching \
    --mamba-ssm-cache-dtype bfloat16 --mamba-cache-mode align > /workspace/vllm_llm.log 2>&1 &
  echo "LLM iniciada (util $LLM_UTIL)"
fi

# embedding ao lado da LLM (so com EMBED_MODE=gpu): espera a LLM reservar a memoria antes de subir
if [ "$EMBED_MODE" = gpu ] && [ -s "$EMBED_DIR/config.json" ] && ! pgrep -f "port 18001" >/dev/null; then
  for _ in $(seq 1 240); do healthy 18000 && break; sleep 5; done
  if vllm serve --help=all 2>/dev/null | grep -q -- '--runner'; then RUN="--runner pooling --convert embed"; else RUN="--task embed"; fi
  # shellcheck disable=SC2086
  nohup vllm serve "$EMBED_DIR" $RUN --served-model-name qwen-embedding --host 127.0.0.1 --port 18001 \
    --tensor-parallel-size 2 --gpu-memory-utilization "$EMBED_UTIL" --max-model-len 8192 --max-num-seqs 64 \
    --max-num-batched-tokens 32768 --hf-overrides '{"is_matryoshka":true}' > /workspace/vllm_embed.log 2>&1 &
  echo "embedding iniciado (util $EMBED_UTIL)"
fi

# vigia independente da VPS: sem atividade por WATCHDOG_IDLE_MIN, para a propria instancia pela API da Vast
if ! pgrep -f llmq-watchdog >/dev/null; then
  cat > /workspace/llmq-watchdog.sh <<'WATCHDOG_EOF'
#__WATCHDOG__
WATCHDOG_EOF
  nohup bash /workspace/llmq-watchdog.sh > /workspace/llmq-watchdog.log 2>&1 &
fi
echo "== bootstrap fim"
