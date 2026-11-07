#!/bin/bash
# Sobe o vLLM do modelo abliterado na GPU FIXA (2x RTX 3090). Versionado aqui e instalado na maquina em /workspace/start_vllm.sh
# pelo llm-queue (app/gpu/llm_runtime.py, por SSH, idempotente) antes de cada partida. SEM vigia de ociosidade: nada aqui para ou destroi a instancia.
#
# Configuracao decidida em 07/11/2026 com medicao (benchmarks/data/raw/segundo-cerebro/20261107-janela2h):
#   vLLM 0.30.0 oficial, SEM MTP (decodificacao especulativa perde de 10 mil tokens para cima e com varios usuarios) e COM --prefix-match-unit 16
#   (4 usuarios a 25 mil tokens: 27,0 -> 32,7 tok/s por usuario; fila de 20 frios 288 -> 234 s).
# Variaveis opcionais: LLM_UTIL (0.90), MAX_LEN (131072), MAX_SEQS (32), MAX_BATCHED (16384), EXTRA_ARGS (substitui os flags extras),
#   LLM_MTP=<n> (liga o MTP com n tokens especulados; padrao desligado). Compatibilidade: o arquivo /workspace/MTP_N com o numero tambem liga.
. /venv/main/bin/activate
mkdir -p /workspace
if [ -z "${EXTRA_ARGS:-}" ]; then
  EXTRA_ARGS="--prefix-match-unit 16"
  MTP_N="${LLM_MTP:-}"
  [ -z "$MTP_N" ] && [ -s /workspace/MTP_N ] && MTP_N="$(tr -dc 0-9 < /workspace/MTP_N)"
  if [ -n "$MTP_N" ] && [ "$MTP_N" != 0 ]; then
    EXTRA_ARGS="$EXTRA_ARGS --speculative-config {\"method\":\"mtp\",\"num_speculative_tokens\":$MTP_N}"
  fi
fi
if pgrep -f "vllm serve" >/dev/null; then echo "vllm ja esta rodando"; exit 0; fi
# shellcheck disable=SC2086
setsid nohup vllm serve /workspace/models/awq --served-model-name qwen-abliterated --host 127.0.0.1 --port 18000 \
  --tensor-parallel-size 2 --gpu-memory-utilization "${LLM_UTIL:-0.90}" --max-model-len "${MAX_LEN:-131072}" --max-num-seqs "${MAX_SEQS:-32}" \
  --max-num-batched-tokens "${MAX_BATCHED:-16384}" --reasoning-parser qwen3 --enable-prefix-caching \
  --mamba-ssm-cache-dtype bfloat16 --mamba-cache-mode align \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml ${EXTRA_ARGS:-} > /workspace/vllm_llm.log 2>&1 < /dev/null &
echo "vllm iniciado $(date -u +%FT%TZ) util=${LLM_UTIL:-0.90} seqs=${MAX_SEQS:-32} batched=${MAX_BATCHED:-16384} extra=${EXTRA_ARGS:-}"
