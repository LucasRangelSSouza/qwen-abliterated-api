# Operação do llm-queue e do llm-access

## Endereços e quem fala com quem
- Público: `https://qwen.rangeltech.net` (`/v1/*` API, `/admin` painel). Só o `llm-access` é exposto.
- Interno: `llm-access.rt:8000` e `llm-queue.rt:8000` (o ponto no nome é exigido pelo anti-SSRF do gateway).
- O `llm-queue` não tem porta publicada. O `kernel-harness` consulta `http://llm-queue.rt:8000/v1/gpu/status` com a chave `harness`.

## Estado e comandos úteis
```bash
curl -H "Authorization: Bearer $CHAVE" https://qwen.rangeltech.net/v1/usage              # uso da própria chave
# painel: Máquinas = estado da GPU, gasto do mês e a tabela de ligadas/desligadas (hora, cold start por etapa, host, custo, motivo)
curl -H "X-Admin-Key: $ADMIN" http://llm-queue.rt:8000/admin/gpu/cycles                    # os mesmos ciclos em JSON (rede interna)
curl -X POST -H "X-Admin-Key: $ADMIN" http://llm-queue.rt:8000/admin/gpu/stop              # para a instância (guarda o disco)
curl -X POST -H "X-Admin-Key: $ADMIN" http://llm-queue.rt:8000/admin/gpu/destroy           # destrói (para de pagar disco)
```
`docker stop llm-queue` **não** desliga a GPU. Use o comando acima, o painel da Vast ou espere o vigia de dentro da GPU (45 min sem atividade).

## Chaves e limites
Toda chave nova nasce na tela **Pessoas** (nome, observação, limite de 5 h e/ou semanal em tokens). A chave aparece uma única vez. Sem limite marcado a pessoa não tem limite.
A chave do dono (`OWNER_KEY_SHA256`) e a do LUAR (`PLATFORM_KEY_SHA256`) valem mesmo se o banco falhar. O banco é um SQLite em `/opt/platform/data/llm-access` (backup: do dono).

## Variáveis que mais importam (`/opt/platform/secrets/llm-queue.env`)
`GPU_MAX_ALLIN_DPH_USD` (0,45), `GPU_MAX_DAILY_USD` (12), `GPU_MONTHLY_BUDGET_USD` (80), `GPU_MIN_CREDIT_USD` (saldo mínimo para ligar), `GPU_IDLE_MIN` (30),
`GPU_STOPPED_TTL_H`/`GPU_STOPPED_MAX_USD` (quanto guardar a instância parada), `GPU_EMBED_MODE` (`off` ou `gpu`), `GPU_IMAGE` (imagem da GPU),
`GPU_DRIVE_REMOTE` + `RCLONE_CONFIG_B64` (pesos pelo Drive, com Hugging Face de reserva e limite de 20 min), `GPU_WAIT_MAX_S` (espera máxima do cliente, 2400 em produção: a imagem sozinha leva de 818 a 1.309 s em alguns hosts), `GPU_RUNNING_TIMEOUT_S` (900), `GPU_CONNECT_RETRIES` (troca de oferta no mesmo ciclo), `NOTICE_FIELDS`/`NOTICE_EVERY_S` (aviso de espera).

## Falhas conhecidas da Vast
- Religar/parar devolvem HTTP 200 mesmo quando falham: ler `success`/`error` do corpo (`resources_unavailable`).
- O `onstart` é gravado na criação: cada instância leva uma impressão (`spec`) e é destruída, não religada, se o script mudar.
- Hosts: erro de GPU (CDI) ou de registro de imagem são destruídos na hora e marcados como ruins por 7 dias; timeout genérico por 24 h.
- Poucas ofertas abaixo do teto: o pedido recebe `503` claro; o preço nunca sobe.
- Portas e endereço SSH mudam a cada partida; o controlador relê e usa túnel SSH.

## Saldo baixo
O `llm-queue` recusa ligar quando o saldo da Vast cai abaixo de `GPU_MIN_CREDIT_USD`. Sem recarga a GPU não liga: o aviso vem no log e no alerta (Google Chat, se o webhook estiver configurado).

## Gateway do LUAR
O provedor `qq` do `llm-gateway` (tenant `platform`) aponta para `http://llm-access.rt:8000/v1` com a chave `PLATFORM`. Para voltar direto para a fila: `python studies/coldstart/repoint_gateway.py --rollback` (precisa de `LLMQ_KEYS_JSON` com a chave antiga do gateway no queue).

## Testar uma acordada completa (gasta saldo)
`studies/coldstart/wake_test.py --label x --env '{...}' --hermes --limits [--embed] --out resultado.json`: liga a GPU pela cadeia real, mede o aviso de espera, roda o Hermes e os limites reais, captura as etapas e **destroi a máquina no fim**. Tem guarda de saldo e teto de tempo. Hosts tchecos recusaram SSH em 3 de 3 tentativas em 30/10/2026.
