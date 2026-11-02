#!/bin/bash
# Sobe o vLLM do modelo abliterado nesta GPU fixa. SEM vigia de ociosidade: nada aqui para ou destroi a instancia.
# Variaveis opcionais: LLM_UTIL (0.92), MAX_SEQS (24), MAX_BATCHED (16384), MAX_LEN (65536), EXTRA_ARGS (flags a mais), e qualquer env do vLLM/NCCL.
. /venv/main/bin/activate
mkdir -p /workspace
if pgrep -f "vllm serve" >/dev/null; then echo "vllm ja esta rodando"; exit 0; fi
# shellcheck disable=SC2086
setsid nohup vllm serve /workspace/models/awq --served-model-name qwen-abliterated --host 127.0.0.1 --port 18000 \
  --tensor-parallel-size 2 --gpu-memory-utilization "${LLM_UTIL:-0.92}" --max-model-len "${MAX_LEN:-65536}" --max-num-seqs "${MAX_SEQS:-24}" \
  --max-num-batched-tokens "${MAX_BATCHED:-16384}" --reasoning-parser qwen3 --enable-prefix-caching \
  --mamba-ssm-cache-dtype bfloat16 --mamba-cache-mode align \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml ${EXTRA_ARGS:-} > /workspace/vllm_llm.log 2>&1 < /dev/null &
echo "vllm iniciado $(date -u +%FT%TZ) util=${LLM_UTIL:-0.92} seqs=${MAX_SEQS:-24} batched=${MAX_BATCHED:-16384} extra=${EXTRA_ARGS:-}"
