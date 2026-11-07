# Qwen abliterado: a LLM, a fila e o painel de controle

Este repositório é o sistema inteiro que serve o **Qwen3.8-27B abliterado** como uma API compatível com OpenAI (chat com stream, ferramentas,
raciocínio, embeddings, voz, imagem e vídeo). Ele contém o código de **três coisas**:

| Peça | Pasta | O que é |
|---|---|---|
| **Painel de controle e porta pública** (`llm-access`) | [`access/`](access/) | Serviço que o mundo enxerga em `https://qwen.rangeltech.net`. Confere a chave de API, aplica os limites por pessoa (sessão de 5 h e semana), cuida de usuários, contas, assinaturas, VMs de cliente, e serve o painel `/admin`. |
| **Fila e controlador da GPU** (`llm-queue`) | [`queue/`](queue/) | Serviço interno (sem porta pública). Enfileira os pedidos, fala com a Vast.ai, mantém o túnel SSH até a máquina da GPU, repassa ao vLLM, liga/desliga/vigia a GPU e gera os avisos no stream. |
| **A LLM** (vLLM na GPU) | [`queue/remote/`](queue/remote/) e [`gpu-image/`](gpu-image/) | O servidor de modelo que roda na máquina alugada na Vast: o script de partida versionado (`start_vllm.sh`), o bootstrap da instância e a imagem enxuta. |

**Este repositório não tem chave de deploy nem segredo de produção.** Ele só **constrói e publica** (release com imagem e manifesto).
Quem **implanta** é o repositório `RangelTech/vps_rt_infra`, que é o único que conhece a VPS e guarda as chaves (ver [Release e deploy](#release-e-deploy)).

- [Visão geral e como as peças se ligam](#visão-geral-e-como-as-peças-se-ligam)
- [Onde cada coisa roda](#onde-cada-coisa-roda)
- [Onde ficam os dados](#onde-ficam-os-dados)
- [Chaves e segredos: quem usa o quê](#chaves-e-segredos-quem-usa-o-quê)
- [Como uma requisição percorre o sistema](#como-uma-requisição-percorre-o-sistema)
- [A API pública e o painel](#a-api-pública-e-o-painel)
- [A fila (`llm-queue`)](#a-fila-llm-queue)
- [A GPU e o vLLM](#a-gpu-e-o-vllm)
- [Release e deploy](#release-e-deploy)
- [Backup, recuperação e troca de máquina](#backup-recuperação-e-troca-de-máquina)
- [Como rodar e testar](#como-rodar-e-testar)
- [Mapa do repositório](#mapa-do-repositório)
- [O que ainda não foi verificado](#o-que-ainda-não-foi-verificado)
- [Documentação complementar](#documentação-complementar)

---

## Visão geral e como as peças se ligam

```
                                  INTERNET
                                      │  HTTPS (Traefik, certificado Let's Encrypt)
                                      ▼
   clientes da API ───────▶  llm-access  (VPS)  ◀───── painel /admin (navegador)
   app Segundo Cérebro                │  │
   LUAR / gateway (chave "platform")  │  └──▶ Postgres unificado  (banco llm_access, schema access)
                                      │  chave de CONSUMIDOR (UPSTREAM_KEY) + chave de ADMIN (UPSTREAM_ADMIN_KEY)
                                      ▼
                                 llm-queue  (VPS, rede interna, sem porta publicada)
                                      │  │
   também chamam a fila direto:       │  └──▶ Redis unificado (db 7): configuração em tempo de execução e estado
   llm-gateway, portfolio-api, harness│
                                      │  API da Vast (VAST_API_KEY)  +  túnel SSH (SSH_PRIVATE_KEY_B64)
                                      ▼
                       ┌────────── Vast.ai ──────────┐
                       │  GPU da LLM:  vLLM  :18000   │   chat/ferramentas/raciocínio
                       │               embeddings :18001 · voz (Whisper) :18002
                       │  GPU de mídia: ComfyUI :8188 │   imagem e vídeo (receitas)
                       └──────────────────────────────┘
```

Regras que valem para tudo:
- **Só o `llm-access` é público.** O `llm-queue` e a GPU não aceitam conexão de fora; a GPU só é alcançada por túnel SSH aberto pela fila.
- **Os dois serviços da VPS são containers Docker** (`llm-access` e `llm-queue`), cada um num *stack* do `vps_rt_infra` (`stacks/llm-access`, `stacks/llm-queue`).
- **A máquina da GPU é alugada na Vast.ai.** A fila a adota por id (modo fixo, 24 h) e nunca a destrói nem a aluga sozinha nesse modo.
- Quem chama a fila direto (sem passar pelo painel) é identificado por uma lista de **consumidores** (`CONSUMERS_JSON`): `llm-access`, `llm-gateway`, `portfolio-api`, `harness` e `harness-test`.

## Onde cada coisa roda

| O quê | Onde | Como chega lá |
|---|---|---|
| `llm-access` | VPS Contabo, container `llm-access`, rede interna com alias `llm-access.rt:8000` e rota Traefik para `qwen.rangeltech.net` | imagem `rt/llm-access`, implantada pelo `vps_rt_infra` |
| `llm-queue` | mesma VPS, container `llm-queue`, alias `llm-queue.rt:8000` (o ponto no nome é exigido pelo anti-SSRF do gateway), **sem Traefik** | imagem `rt/llm-queue`, implantada pelo `vps_rt_infra` |
| Postgres e Redis | mesma VPS, containers `postgres` e `redis` (compartilhados com o resto da plataforma) | gerenciados pelo `vps_rt_infra` |
| vLLM (a LLM) | máquina na Vast, `127.0.0.1:18000` dentro dela | `/workspace/start_vllm.sh`, instalado pela fila por SSH (ver [A GPU e o vLLM](#a-gpu-e-o-vllm)) |
| Embeddings, voz | mesma máquina da LLM, portas `18001` e `18002` | embeddings pelo bootstrap quando `GPU_EMBED_MODE=gpu`; voz (faster-whisper) instalada pela fila por SSH |
| Imagem e vídeo | **segunda máquina** da Vast (a "GPU de mídia"), ComfyUI na porta 8188 | fila alcança por túnel (porta local `18188`) |

Nomes, rotas e variáveis de uma peça sempre estão no arquivo `compose.yml` do respectivo `stack` (`access/deploy/compose.yml`, `queue/deploy/compose.yml` neste repo são cópias de referência).

## Onde ficam os dados

| Dado | Onde | Observação |
|---|---|---|
| **Pessoas, chaves de API (só o SHA-256), limites, janelas de uso, log de uso, configurações, planos, contas, assinaturas, VMs, eventos de cobrança, log de segurança** | **Postgres unificado**, banco `llm_access`, schema `access`. Ligado por `DATABASE_URL` (+ `DATABASE_SCHEMA`) | O adaptador é `access/app/store_pg.py` (traduz o SQL do SQLite). Sem `DATABASE_URL`, o painel usa SQLite em `ACCESS_DB_PATH`: **isso é só para teste e desenvolvimento**. |
| Configuração em tempo de execução da GPU (`auto_shutdown`, `idle_min`, `reserved`), livro-razão de gastos, ciclos de ligar/desligar, estado das filas | **Redis unificado**, base `7`, chaves com prefixo `llmq:` | A fonte da verdade da configuração é o Redis; o ambiente só dá o valor inicial. |
| Pesos do modelo, caches de compilação | disco da máquina da Vast (`/workspace/models/...`) | baixados pelo bootstrap (Google Drive via rclone, com Hugging Face de reserva) |
| Script de partida do vLLM | `/workspace/start_vllm.sh` na máquina da Vast; fonte em [`queue/remote/start_vllm.sh`](queue/remote/start_vllm.sh) | a fila instala por SSH antes de cada partida (idempotente, guarda `.bak_<data>`) |
| Segredos | VPS, `/opt/platform/secrets/llm-access.env` e `llm-queue.env` | nunca neste repositório (ver abaixo) |

> Um arquivo `access.db` antigo ainda existe em `/opt/platform/data/llm-access/` na VPS. É um **resto da migração para o Postgres** e deixou de ser escrito; não é a fonte dos dados.

## Chaves e segredos: quem usa o quê

Os valores **não** estão aqui. Esta é a lista de nomes, de quem é cada um e onde mora. Cópias existem no cofre privado do dono e no Infisical.

### `llm-access` (arquivo `/opt/platform/secrets/llm-access.env` na VPS)

| Variável | Para que serve |
|---|---|
| `DATABASE_URL`, `DATABASE_SCHEMA` | Conexão com o Postgres unificado (banco `llm_access`, schema `access`). Vazia = SQLite local (só testes). |
| `ACCESS_DB_PATH` | Caminho do SQLite de teste/legado (`/data/access.db`). Não é usado quando `DATABASE_URL` está definida. |
| `UPSTREAM_URL` | Endereço da fila (`http://llm-queue.rt:8000`). |
| `UPSTREAM_KEY` | Chave do **consumidor `llm-access`** na fila. Todo o tráfego do painel chega à fila por este único consumidor. |
| `UPSTREAM_ADMIN_KEY` | `X-Admin-Key` da fila: consulta estado da GPU, ciclos e altera a configuração da GPU pelo painel. |
| `ADMIN_PASSWORD` | Senha do painel `/admin`. |
| `SESSION_SECRET` | Assina o cookie de sessão do painel. |
| `OWNER_KEY_SHA256` | SHA-256 da **chave do dono**: sem limite, vale mesmo se o banco falhar. |
| `PLATFORM_KEY_SHA256` | SHA-256 da **chave da plataforma** (usada pelo gateway do LUAR). Também vale sem banco. |
| `SECRETS_KEY` | Chave que cifra segredos guardados no banco (ex.: dados das VMs de cliente). |
| `CAKTO_WEBHOOK_SECRET` | Valida o webhook de cobrança da Cakto (`POST /v1/billing/cakto`). |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_FROM`, `SMTP_PASSWORD` | E-mail de convite e recuperação de senha. |
| `ACCESS_QA_MODE` | `1` libera janelas menores que 1 h (só QA). |

Chaves **de pessoa** não são variáveis: são criadas na tela **Pessoas** do painel, aparecem uma única vez e ficam no banco só como SHA-256 (tabela `keys`).

### `llm-queue` (arquivo `/opt/platform/secrets/llm-queue.env` na VPS)

| Variável | Para que serve |
|---|---|
| `REDIS_URL` | Redis unificado (base 7). |
| `CONSUMERS_JSON` | Lista de consumidores autorizados a chamar a fila (nome, prioridade, chave em SHA-256). |
| `ADMIN_KEY_SHA256` | SHA-256 da chave de admin da fila (cabeçalho `X-Admin-Key`). |
| `VAST_API_KEY` | API da Vast.ai: consultar, ligar e (fora do modo fixo) alugar máquinas, ver saldo e chaves SSH. |
| `SSH_PRIVATE_KEY_B64` | Chave **privada** SSH em base64, gravada em `/tmp` com permissão 0600 ao subir. Abre o túnel e os comandos até a GPU. |
| `RCLONE_CONFIG_B64`, `GPU_DRIVE_REMOTE` | Baixar pesos pelo Google Drive. |
| `GOOGLE_CHAT_WEBHOOK` | Alertas (GPU degradada, saldo baixo, chave SSH ausente). |
| `GPU_STATIC_INSTANCE_ID` | **Id da máquina fixa da LLM na Vast.** Vazio = modo sob demanda. Não muda em tempo de execução: trocar a máquina é trocar este valor e reiniciar o container. |
| `MEDIA_INSTANCE_ID` | Id da máquina de mídia (imagem e vídeo). |
| Demais `GPU_*`, `CHAT_*`, `MEDIA_*`, `STT_*`, `NOTICE_*` | Limites, tetos de gasto, tamanhos de fila, contexto máximo, avisos no stream. Lista completa e padrões em [`queue/app/config.py`](queue/app/config.py) e em [`docs/OPERACAO.md`](docs/OPERACAO.md). |

### Chave SSH da Vast: como funciona de verdade

- O par de chaves SSH é **da conta da Vast**. A chave **pública** precisa estar cadastrada na conta (a fila confere com `GET /v0/ssh/` e alerta `ssh_key` se faltar; ela **não cadastra** a chave). Toda máquina nova da conta herda a chave.
- A chave **privada** é a `SSH_PRIVATE_KEY_B64` da fila, na VPS.
- A API da Vast **sozinha não basta** para operar uma máquina: ela liga/desliga/aluga, mas o que roda dentro (vLLM, pesos, vigia) é alcançado por SSH.

### Quem tem a chave de deploy da VPS

Somente o repositório `vps_rt_infra` (GitHub Environment `production`, secret `VPS_SSH_PRIVATE_KEY`; usuário `deploy`). **Este repositório não guarda nenhuma credencial de deploy**; o `release.yml` usa só o `github.token`.

## Como uma requisição percorre o sistema

1. O cliente chama `POST https://qwen.rangeltech.net/v1/chat/completions` com `Authorization: Bearer <chave>`.
2. O `llm-access` identifica a chave: dono, plataforma (ambas por SHA-256 do ambiente, valem sem banco) ou pessoa (SHA-256 na tabela `keys`).
3. Aplica os **limites**: sessão de 5 h (abre no primeiro pedido e reabre 5 h depois) e semana de 7 dias, os dois relógios independentes; estourar qualquer um devolve `429` com `Retry-After` e a hora de reabertura. O saldo é um só por chave. Os tokens são reservados antes e acertados depois; um pedido que falha antes de qualquer token real não abre janela nem cobra.
4. Repassa à fila com a chave de consumidor `llm-access` (`UPSTREAM_KEY`).
5. A fila enfileira (`CHAT_MAX_INFLIGHT`, `CHAT_PER_CONSUMER`, `CHAT_QUEUE_MAX`), garante que a GPU está pronta e repassa pelo túnel SSH ao vLLM em `127.0.0.1:18000`.
6. O stream volta pelo mesmo caminho. O painel registra **só tokens e metadados**; nenhum texto de mensagem vai ao banco nem ao log.

**Se a GPU estiver desligada** (modo sob demanda): o cabeçalho 200 sai na hora; em stream, a primeira linha é um aviso fixo em português ("o computador da IA estava desligado e está sendo ligado…"), repetido com a etapa e a estimativa. Ele vai como `delta.reasoning`, então aparece como "pensando" e **não entra no histórico**; o campo `x_queue` traz estado e estimativa. Com `X-Queue-Notice: content` o aviso vai como texto da resposta; com `X-Queue-Wait: 0` o pedido acorda a GPU e volta com `202` e `Retry-After`. Em respostas JSON, espaços a cada 15 s mantêm a conexão viva.

## A API pública e o painel

**API pública** (todas pedem chave, exceto `/health`):

| Rota | Função |
|---|---|
| `POST /v1/chat/completions` | Chat com stream, ferramentas e raciocínio. |
| `POST /v1/embeddings` | Embeddings (Qwen3-Embedding). |
| `POST /v1/audio/transcriptions` | Voz para texto (faster-whisper na máquina da LLM). |
| `POST /v1/images/generations`, `POST /v1/videos/generations` | Imagem e vídeo (ComfyUI na máquina de mídia). Receitas de vídeo: `ltx-video` (rápido), `wan22-ti2v` (alta qualidade), `openjutsu-swap` (troca de personagem). Assíncrono com `async:true`. |
| `GET /v1/jobs/{id}`, `/file`, `/image`, `GET /v1/media/jobs/{id}` | Acompanhar e baixar resultados de mídia. |
| `GET /v1/models`, `GET /v1/usage` | Modelos e uso/reabertura da própria chave. |
| `POST /v1/auth/login`, `/forgot`, `/verify`, `/set-password`, `GET /v1/auth/whoami` | Login do app por conta (e-mail e senha), com chave por computador. |
| `POST /v1/enroll` | Inscrição/instalação de cliente. |
| `POST /v1/billing/cakto` | Webhook da Cakto (assinatura de cliente). |
| `GET /health` | Saúde (`{"ok":true}`). |

**Painel `/admin`** (React, `access/panel/`, servido pelo próprio `llm-access`; exige senha e HTTPS). Telas: **Pessoas** (cria chave, limite de 5 h e semanal, suspender/retomar, girar chave, zerar janelas), **Uso**, **Máquinas** (estado da GPU, gasto do mês, tabela de ligadas/desligadas com cold start por etapa, interruptor de desligamento automático, reservar), **Assinantes**, **VMs** (VMs de cliente: provisionar, suspender, cancelar), **Configurações** (planos, cobrança, nível de segurança) e **Imagem da máquina**. A API do painel fica em `/admin/api/...`.

## A fila (`llm-queue`)

Endpoints (rede interna): `/v1/chat/completions`, `/v1/embeddings`, `/v1/audio/transcriptions`, `/v1/images|videos/generations`, `/v1/jobs/*`, `/v1/media/jobs/*`, `/v1/models`, `/v1/gpu/status` (estado para quem quer avisar o usuário), `/health`, `/metrics`; e, com `X-Admin-Key`: `/admin/gpu/status|config|cycles|jobs`, `/admin/media/*`, `/admin/billing`, `/admin/metrics`, `POST /admin/gpu/config`, `POST /admin/gpu/stop|destroy` (no modo fixo exigem `{"i_know_this_loses_the_host": true}`).

Módulos principais: [`queue/app/gpu/controller.py`](queue/app/gpu/controller.py) (máquina de estados, modo fixo, partida e vigia), `vast.py` (API da Vast), `tunnel.py` (túnel e comandos SSH), `hosts.py` (hosts ruins), `ledger.py` (gastos), `cycles.py` (log de ciclos), `llm_runtime.py` (instala o `start_vllm.sh`), `stt.py` (ditado), [`queue/app/scheduler.py`](queue/app/scheduler.py) (filas por consumidor), `media.py` e `workflows/` (imagem e vídeo), `notice.py` (aviso no stream).

## A GPU e o vLLM

**Modo fixo (produção).** `GPU_STATIC_INSTANCE_ID` preenchido: a fila adota a máquina pelo id, abre o túnel com o endereço que a API da Vast informa e fica `READY`. Nunca cria, aluga, para ou destrói a máquina (parar/destruir pelo admin exigem confirmação explícita e perdem a máquina). Se o vLLM morrer, a fila **instala o `start_vllm.sh` versionado e o reinicia por SSH** (no máximo uma vez a cada `GPU_STATIC_LLM_RESTART_MIN_S`). Detalhes e falhas conhecidas: [`docs/OPERACAO.md`](docs/OPERACAO.md).

**Configuração do vLLM em produção** ([`queue/remote/start_vllm.sh`](queue/remote/start_vllm.sh)): vLLM 0.30 oficial, modelo `Qwen3.8-27B-abliterated` AWQ 4 bits, 2×RTX 3090 (tensor-parallel 2), contexto `131072`, `max-num-seqs 32`, `gpu-memory-utilization 0.90`, `--reasoning-parser qwen3`, `--tool-call-parser qwen3_xml`, cache de prefixo ligado com `--prefix-match-unit 16`, **sem MTP**. A decisão foi medida em 07/11/2026 (decode, concorrência, fila de 20 pedidos): o MTP perde de 10 mil tokens de contexto para cima e com vários usuários. Dados e método em `benchmarks/data/raw/segundo-cerebro/20261107-janela2h/` (repositório privado `benchmarks`) e na seção 12 da spec do Segundo Cérebro. Variáveis do script: `LLM_UTIL`, `MAX_LEN`, `MAX_SEQS`, `MAX_BATCHED`, `EXTRA_ARGS`, e `LLM_MTP=<n>` (ou o arquivo `/workspace/MTP_N`) para religar o MTP.

**Modo sob demanda** (`GPU_STATIC_INSTANCE_ID` vazio, legado): a primeira requisição aluga uma 2×3090 abaixo do teto de preço, o `onstart` ([`queue/remote/bootstrap.sh`](queue/remote/bootstrap.sh)) baixa os pesos e sobe o vLLM, e a ociosidade desliga. O `onstart` é gravado na criação e entra na "impressão" (`spec_id`) da instância: **mudar o `bootstrap.sh` faz o controlador destruir e recriar instâncias sob demanda**. Por isso o `start_vllm.sh` não é instalado pelo `bootstrap.sh`, e sim por SSH.

**Imagem da GPU** (`gpu-image/`): versão enxuta da imagem `vastai/vllm` (sem kernels de GPUs Blackwell), gerada e provada por `gpu-image.yml`.

## Release e deploy

```
você edita e faz push em main (queue/, access/ ou VERSION_SERIES)
        │
        ▼  .github/workflows/release.yml  (neste repositório)
   testa (queue e access), constrói a imagem de cada serviço,
   gera release-manifest.<serviço>.json (sha256, saúde, variáveis exigidas)
   e publica uma release  v<série>.<N>   (série em VERSION_SERIES, N sobe sozinho)
        │   NÃO implanta nada
        ▼
   repositório vps_rt_infra  →  Actions  →  "Deploy LLM"
   escolha: llm-tudo | llm-access | llm-queue   (+ versão opcional, + dry_run)
   o botão busca a release na hora, valida o manifesto, baixa o artefato na VPS,
   troca o container, confere o /health e reverte sozinho se falhar.
   Ordem do llm-tudo: painel → fila (para se um passo falhar).
```

- **Numeração:** `v<série>.<N>` (ex.: `v0.5.1`). Para abrir uma série nova, edite `VERSION_SERIES`. Uma tag `v*` criada à mão também funciona.
- **Qual versão está em produção:** `releases/<serviço>.lock` no `vps_rt_infra` (atualizado após cada deploy bem-sucedido) e `docker ps` na VPS (a tag da imagem é o início do `image_id`). Mudanças só no `queue/` deixam a imagem do `llm-access` com outro id mesmo sem mudança de código.
- **Voltar uma versão:** rode "Deploy LLM" com o campo de versão.
- **Mudar a configuração do vLLM** (`start_vllm.sh`): edite o arquivo, publique, implante a fila; a nova versão é instalada na máquina na **próxima partida** do vLLM. Para aplicar na hora é preciso reiniciar o vLLM (ver [O que ainda não foi verificado](#o-que-ainda-não-foi-verificado)).

## Backup, recuperação e troca de máquina

- **Painel (Postgres):** o `backup.sh` do `vps_rt_infra` faz um dump lógico diário de cada banco, incluindo `postgres.llm_access.dump`, e envia ao Google Drive com restic (via rclone). Uma prova semanal (`backup-verify`) restaura o último snapshot num Postgres descartável.
- **Fila (Redis e segredos):** a pasta do Redis e `/opt/platform/secrets` entram no mesmo backup.
- **Perder a VPS:** recria-se pelo `vps_rt_infra`; o painel volta a partir do dump; os segredos vêm do backup/cofre.
- **Trocar a máquina da GPU (resumo; o caminho completo ainda não foi exercitado):**
  1. Alugar a máquina na Vast (console ou API) com a chave SSH da conta; ela herda a chave pública cadastrada.
  2. Garantir os pesos em `/workspace/models/awq` (bootstrap/Drive) e o ambiente do vLLM.
  3. Trocar `GPU_STATIC_INSTANCE_ID` no `llm-queue.env` da VPS e reiniciar o container `llm-queue`.
  4. A fila instala o `start_vllm.sh` e sobe o vLLM; conferir `GET /v1/gpu/status`.
  5. Rodar uma conversa e uma chamada de ferramenta antes de liberar.

## Como rodar e testar

Nada abaixo gasta dinheiro nem precisa de GPU.

```bash
cd queue  && pip install -r requirements-dev.txt && python -m pytest -q     # Vast e GPU simuladas, ofertas reais em tests/data
cd access && pip install -r requirements-dev.txt && python -m pytest -q     # janelas, concorrência, stream cortado, painel (upstream falso); roda em SQLite e em Postgres
cd access/panel && npm ci && VITE_MOCK=1 npm run dev                        # painel com dados de mentira em http://localhost:5175/admin/ (senha: admin)
bash queue/scripts/verify.sh && bash access/scripts/verify.sh               # o gate do release: testes + a imagem sobe e responde /health
```

O QA dos limites usa janelas curtas (`ACCESS_QA_MODE=1`) e limites de 100 e 200 tokens, para ver o bloqueio e a reabertura sem esperar horas.
O script `access/scripts/migrate_sqlite_to_pg.py` migra um SQLite para o Postgres com conferência tabela a tabela.

## Mapa do repositório

```
access/        llm-access (FastAPI): app/ (auth, chaves, janelas, contas, planos, VMs, cobrança, proxy), panel/ (React), deploy/compose.yml, scripts/
queue/         llm-queue (FastAPI): app/ (controlador da GPU, scheduler, mídia, aviso no stream), remote/ (start_vllm.sh, bootstrap.sh, vigia), deploy/compose.yml
gpu-image/     gerador do Dockerfile da imagem enxuta da GPU
docs/          OPERACAO, RUNBOOK, CICD, ARCHITECTURE, DECISOES, BENCHMARKS, RESULTS, SELF_HOSTING e o estudo original do GB10
studies/       coldstart: medições de cold start na Vast
legacy/gb10/   Terraform, Traefik e scripts da GB10 apagada (só histórico; não é o caminho atual)
tests/ reports/  scripts e dados brutos dos benchmarks do GB10
VERSION_SERIES série atual da numeração das releases
.github/       ci.yml, validate.yml (a cada push/PR), release.yml (release por push), gpu-image*.yml e inspect-base-image.yml (manuais)
```

## O que ainda não foi verificado

Para ninguém achar que está pronto o que não está:

- **Reiniciar o vLLM por botão:** a fila instala o `start_vllm.sh` e o reinicia sozinha quando o vLLM morre, mas **não há comando de admin** para reiniciar o vLLM e aplicar uma mudança de configuração na hora. Hoje isso é por SSH. Os segredos da fila ficam na VPS, não no GitHub, então um botão no "Deploy LLM" precisa de desenho.
- **Recriar a máquina da GPU do zero** (alugar, pesos, vLLM) não foi exercitado de ponta a ponta com a configuração atual.
- **Terraform de DNS** (`vps_rt_infra`): falha ao tentar criar o registro `qwen`, que já existe; os subdomínios funcionam, só não dá para criar um novo pelo pipeline até corrigir.
- **`docs/OPERACAO.md`** ainda cita o SQLite do painel em alguns pontos; vale a regra desta página: o painel usa o Postgres.

## Documentação complementar

[`docs/OPERACAO.md`](docs/OPERACAO.md) (operação, GPU fixa, falhas da Vast) · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) · [`docs/CICD.md`](docs/CICD.md) · [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`access/DESIGN.md`](access/DESIGN.md) (contrato do painel e regras de janela) · [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) e [`docs/RESULTS.md`](docs/RESULTS.md) (por que esta configuração) · [`docs/GB10_STUDY.md`](docs/GB10_STUDY.md).

## Segurança

Repositório público: sem segredos, sem endereços privados, ofertas de teste sanitizadas (`queue/tests/data`). O painel exige senha e HTTPS, o `llm-queue` não tem porta publicada, as chaves de pessoa ficam só como SHA-256 e nenhum texto de mensagem é gravado (só tokens e metadados). Saídas de modelo abliterado nunca entram no repositório, só agregados.
