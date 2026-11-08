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
A chave do dono (`OWNER_KEY_SHA256`) e a do LUAR (`PLATFORM_KEY_SHA256`) valem mesmo se o banco falhar. O banco é o **Postgres unificado** (banco `llm_access`, schema `access`, via `DATABASE_URL`), com dump lógico diário no backup da VPS. O `access.db` que ainda existe em `/opt/platform/data/llm-access` é resto da migração e não é mais escrito.

## Variáveis que mais importam (`/opt/platform/secrets/llm-queue.env`)
`GPU_MAX_ALLIN_DPH_USD` (0,45), `GPU_MAX_DAILY_USD` (12), `GPU_MONTHLY_BUDGET_USD` (80), `GPU_MIN_CREDIT_USD` (saldo mínimo para ligar), `GPU_IDLE_MIN` (30, só o valor inicial: ver "GPU fixa e interruptor"),
`GPU_STOPPED_TTL_H`/`GPU_STOPPED_MAX_USD` (quanto guardar a instância parada), `GPU_EMBED_MODE` (`off` ou `gpu`), `GPU_IMAGE` (imagem da GPU),
`GPU_DRIVE_REMOTE` + `RCLONE_CONFIG_B64` (pesos pelo Drive, com Hugging Face de reserva e limite de 20 min), `GPU_WAIT_MAX_S` (espera máxima do cliente, 2400 em produção: a imagem sozinha leva de 818 a 1.309 s em alguns hosts), `GPU_RUNNING_TIMEOUT_S` (900), `GPU_CONNECT_RETRIES` (troca de oferta no mesmo ciclo), `NOTICE_FIELDS`/`NOTICE_EVERY_S` (aviso de espera).

## GPU fixa e interruptor
Duas peças que andam juntas: o **modo de instância fixa** (máquina alugada 24/7 que a fila nunca pode perder) e o **interruptor de desligamento automático** (vale também para a GPU sob demanda).

**Variáveis (`llm-queue.env`)**
- `GPU_STATIC_INSTANCE_ID`: id inteiro da instância fixa na Vast. Vazio = modo fixo desligado (comportamento sob demanda de sempre).
- `GPU_AUTO_SHUTDOWN` (padrão `false`), `GPU_IDLE_MIN` (30) e `GPU_RESERVED` (padrão `false`): só os **valores iniciais** da configuração em tempo de execução.
- `GPU_STATIC_RETRY_S` (15) e `GPU_STATIC_RETRY_MAX_S` (300): recuo entre tentativas de reconectar no modo fixo (dobra a cada falha até o máximo).

**Configuração em tempo de execução.** A fonte da verdade é o Redis do `llm-queue` (chave `llmq:config`); o ambiente só dá o valor de partida. `GET` e `POST /admin/gpu/config` (cabeçalho `X-Admin-Key`) leem e alteram `auto_shutdown` (bool), `idle_min` (1 a 1440) e `reserved` (bool), sem reiniciar; cada mudança vai para o log (`config da GPU alterada por ...`). O bloco `config` também sai em `GET /v1/gpu/status` e `GET /admin/gpu/status`. No painel: **Configurações > GPU**, que apenas repassa ao queue (`GET`/`PUT /admin/api/gpu-config`, sem cópia no SQLite).
- `auto_shutdown=false` (padrão): **nenhum** caminho automático para a GPU (ociosidade, sessão máxima, teto diário, TTL da instância parada) e o vigia `watchdog.sh` nem entra no `onstart` das instâncias novas. Os tetos diário e mensal e o saldo mínimo continuam barrando **partidas novas**, mas nunca param uma instância ligada. Reparo de host que morreu (queda, sem saúde 3 vezes) segue como antes no modo sob demanda.
- `auto_shutdown=true`: volta ao comportamento anterior. Ligar o interruptor reinicia a janela de ociosidade. Instância criada com o vigia interno continua sujeita a ele mesmo com o interruptor desligado depois (o queue avisa no alerta `watchdog_ativo`).
- `reserved=true`: o Lucas parou só o processo do LLM para usar a GPU em outra coisa. Todo pedido (chat e embeddings) recebe `503` com `Retry-After: 300` e o corpo `GPU reservada para outra carga`; o controlador não religa, não confere saúde, não alerta nem mexe na instância. Ao voltar a `false`, a saúde é conferida na hora.

**Modo fixo (`GPU_STATIC_INSTANCE_ID` preenchido)**
- Adota a instância pelo id, **ignorando rótulo e spec**, abre o túnel SSH com `ssh_host`/`ssh_port` que a API da Vast informa, confere a saúde e fica `READY`.
- **Nunca** cria instância, nunca aluga (nem busca ofertas), nunca destrói, nunca chama `power(stopped)`, nunca marca o host como ruim e não grava a máquina no livro-razão (o custo dela fica fora dos tetos do queue). O interruptor e o TTL não se aplicam.
- Falha de saúde (3 verificações), queda do host, túnel ou SSH quebrado: **alerta** (Google Chat) e nova tentativa com recuo. O estado vira `LOADING`, `GET /v1/gpu/status` traz `degraded: {reason, detail, since, attempts, retry_in_s}` e os pedidos recebem `503` rápido (`gpu_fixa_<motivo>`) em vez de ficar pendurados.
- Instância parada por fora (`exited`): só **religar** é permitido (`power(running)`, no máximo uma vez a cada `GPU_STATIC_RETRY_MAX_S`); nunca parar.
- `POST /admin/gpu/stop` e `POST /admin/gpu/destroy` devolvem `409` (`static_instance_protected`) a menos que o corpo traga `{"i_know_this_loses_the_host": true}`. Confirmado, o controlador executa e fica em pausa até o serviço reiniciar. O painel não tem esses botões.
- Recuperação: voltando a responder, o estado volta a `READY` sozinho e sai o alerta `static_recuperada`.
- Tudo isso vale mesmo com `auto_shutdown=true`.

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

## GPU fixa: afinação e armadilhas (02/11/2026)
- `CHAT_MAX_INFLIGHT`, `CHAT_PER_CONSUMER`, `CHAT_QUEUE_MAX`, `EMBED_QUEUE_MAX` e `MAX_CONTEXT_TOKENS` valem de verdade desde a v0.3.2. Todo o tráfego chega ao llm-queue por **um** consumidor (o llm-access): `CHAT_PER_CONSUMER` precisa ser igual a `CHAT_MAX_INFLIGHT` (32 em produção), senão só 3 chats rodam juntos. `MAX_CONTEXT_TOKENS` acompanha o `--max-model-len` do vLLM (131072).
- Valores de produção da GPU fixa: `CHAT_MAX_INFLIGHT=32`, `CHAT_PER_CONSUMER=32`, `CHAT_QUEUE_MAX=200`, `MAX_CONTEXT_TOKENS=131072`, `GPU_STATIC_INSTANCE_ID=<id>`, `GPU_AUTO_SHUTDOWN=false`.
- O `kernel-harness` usa por padrão janela de 200 mil tokens e comprime aos 60 % (120 mil); como o vLLM aceita 131 mil, a compressão acontece antes do limite. A janela do harness é global (vale para todos os modelos): não baixar para o contexto do Qwen.
- Reiniciar o vLLM por dentro: `kill -9` no servidor deixa `VLLM::EngineCore` e `VLLM::Worker_*` segurando a memória das placas; matar também esses processos (pelo PID do contêiner, não pelo do `nvidia-smi`). Durante o experimento, ligar o modo **reservada** no painel (Configurações → GPU) para a fila não alertar nem devolver erro aos usuários.
- Antes de alugar uma 2×3090 para tensor parallel, ver `bw_nvlink` na oferta (0 = sem NVLink e sem P2P) e `cuda_max_good` (≥ 13 para a imagem atual).

## Worker de vídeo sob demanda (08/11/2026)
Vídeo de qualidade máxima (**Wan 2.2 A14B**, receita `wan22-a14b`) roda numa **RTX PRO 6000 de 96 GB alugada só quando há pedido** e destruída depois de 10 min ocioso. Imagem, vídeo rápido (LTX) e troca de personagem continuam na GPU fixa de mídia (`MEDIA_INSTANCE_ID`). Código em `queue/app/gpu/worker.py`; instalação da máquina em `queue/remote/worker-provision.sh`.
- **Liga com** `WORKER_ENABLED=true` no `llm-queue.env` (precisa de `VAST_API_KEY` e `SSH_PRIVATE_KEY_B64`, os mesmos de sempre). Vídeo sem `recipe` vai para o worker; `recipe: "ltx-video"` pede o rápido na GPU fixa.
- **Variáveis** (padrão): `WORKER_MAX_DPH_USD=1.80` (teto do preço TOTAL GPU+disco), `WORKER_MIN_DOWN_MBPS=1000`, `WORKER_MIN_RELIABILITY=0.95`, `WORKER_IDLE_S=600`, `WORKER_MAX_AGE_H=8`, `WORKER_MIN_CREDIT_USD=3`, `WORKER_WAIT_S=3600` (quanto o pedido espera por uma máquina), `WORKER_START_TIMEOUT_S=1500`, `WORKER_DISK_GB=120`, `WORKER_GPU_NAMES="RTX PRO 6000 S,RTX PRO 6000 WS"` (a Max-Q é limitada em potência e fica de fora).
- **Medido (08/11/2026):** instalar 81 GB de modelos em ~145 s numa rede de 18 Gbps; clipe de 5 s em 832x480 = 236–254 s; em 1280x720 = 852 s (S, Nevada) a 1145 s (WS, Ontário); 16 fps. Fluxo real de produção: partida a frio ~8 min até começar a gerar; 480p pronto em 12 min; 720p em ~20 min. A previsão ao cliente soma a partida da máquina (~5 min), os pedidos na frente e o próprio vídeo.
- **Qualidade (`quality`):** `auto` = 720p para até 5 s e 480p acima disso (a produção inteira cabe em ~20 min); `alta` = 720p sempre; `padrao` = 480p. Sem imagem do cliente, o Flux cria o primeiro quadro na própria máquina.
- **Custo:** só enquanto a máquina existe (aluguel + 10 min de espera). Cada sessão grava `{instância, custo, motivo}` em `llmq:worker:runs` (Redis base 7). `GET /admin/media/status` traz `worker` (estado, estágio, preço/h, custo da sessão, quando será destruída).
- **Guardas contra vazamento:** nunca aluga acima do teto nem com saldo baixo; toda tentativa que falha DESTRÓI a máquina antes de tentar outra; o vigia destrói qualquer instância com o rótulo `mi-worker-video` que não seja a atual (5 min depois de o serviço subir) e a que passar de `WORKER_MAX_AGE_H`. Para conferir à mão: `GET /v1/instances/` da Vast e procurar o rótulo.
- **Falha:** sem saldo, sem oferta dentro do teto ou instalação que não termina → o pedido falha com a mensagem padrão ao cliente (sem texto de infraestrutura) e o motivo fica em `worker.last_error`. Recarregar saldo ou subir `WORKER_MAX_DPH_USD` resolve.
- **Risco conhecido:** se o `llm-queue` morrer com a máquina alugada e não voltar, ela segue cobrando (US$ ~1,5/h) até alguém destruí-la; o contêiner tem `restart: unless-stopped`, e ao voltar o vigia a adota ou destrói. Os 81 GB vêm do Hugging Face a cada partida: se ele cair, a partida falha e tenta outra máquina.
