#!/bin/bash
# llmq-watchdog: rede de seguranca DENTRO da GPU (Spec 13 §5.2 item 1). Independe da VPS: se o llm-queue morrer com a GPU ligada,
# esta instancia se para sozinha apos WATCHDOG_IDLE_MIN sem atividade. Atividade = requisicao rodando/esperando no vLLM OU contador de
# requisicoes concluidas mudando (pega pedidos curtos entre duas leituras).
# Variaveis: WATCHDOG_IDLE_MIN (45), WATCHDOG_POLL_S (60), WATCHDOG_IDLE_S (sobrescreve em segundos, testes),
#            WATCHDOG_METRICS_URLS (padrao LLM e embedding), WATCHDOG_DRYRUN=1 (so imprime), CONTAINER_ID/CONTAINER_API_KEY (da Vast).
# llmq-watchdog
POLL=${WATCHDOG_POLL_S:-60}
IDLE_S=${WATCHDOG_IDLE_S:-$(( ${WATCHDOG_IDLE_MIN:-45} * 60 ))}
URLS=${WATCHDOG_METRICS_URLS:-"http://127.0.0.1:18000/metrics http://127.0.0.1:18001/metrics"}
last_active=$(date +%s)
last_total=-1

while true; do
  sleep "$POLL"
  busy=0; total=0
  for u in $URLS; do
    m=$(curl -sf --max-time 5 "$u" 2>/dev/null) || continue
    r=$(printf '%s\n' "$m" | awk '/^vllm:num_requests_(running|waiting)\{/{s+=$NF} END{print s+0}')
    t=$(printf '%s\n' "$m" | awk '/^vllm:request_success_total\{/{s+=$NF} END{print s+0}')
    [ "${r%.*}" != "0" ] && busy=1
    total=$(( total + ${t%.*} ))
  done
  [ "$total" != "$last_total" ] && [ "$last_total" != "-1" ] && busy=1
  last_total=$total
  now=$(date +%s)
  [ "$busy" = 1 ] && last_active=$now
  idle=$(( now - last_active ))
  if [ "$idle" -ge "$IDLE_S" ]; then
    echo "WOULD_STOP idle=${idle}s"
    if [ "${WATCHDOG_DRYRUN:-0}" = 1 ]; then exit 0; fi
    curl -s -X PUT "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" \
      -H "Authorization: Bearer ${CONTAINER_API_KEY}" -H "Content-Type: application/json" -d '{"state":"stopped"}'
    sleep 120  # se o stop falhar, tenta de novo no proximo ciclo
  fi
done
