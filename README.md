# Qwen abliterated: fila de IA com GPU sob demanda

API compatível com OpenAI (chat com stream, ferramentas, raciocínio e embeddings) para o **Qwen3.8-27B abliterado**, rodando em
**2×RTX 3090 alugadas na Vast.ai que só existem enquanto alguém usa**. Nada fica ligado 24 horas: a primeira requisição liga a
máquina, a ociosidade desliga, e quem pede durante a subida vê no próprio stream que o computador está sendo ligado e que a espera não é erro.

Este repositório é o sistema inteiro: os dois serviços, a imagem da GPU, o estudo e os benchmarks que levaram a esta configuração,
e a documentação. **Ele não tem chave de deploy nem segredo de produção**: quem implanta é o `vps_rt_infra`, que baixa os artefatos
daqui (releases) e é o único que conhece a VPS.

- [O que roda](#o-que-roda)
- [Como uma requisição percorre o sistema](#como-uma-requisição-percorre-o-sistema)
- [Como rodar e testar](#como-rodar-e-testar)
- [Release e deploy](#release-e-deploy)
- [Benchmarks e por que esta configuração](#benchmarks-e-por-que-esta-configuração)
- [O que foi testado](#o-que-foi-testado)
- [Mapa do repositório](#mapa-do-repositório)
- [Segurança](#segurança)

## O que roda

| Peça | Onde | O que faz |
|---|---|---|
| **`llm-access`** (`access/`) | VPS (Contabo), único serviço público: `https://qwen.rangeltech.net` | Chaves de API por pessoa, limite de **sessão de 5 horas** e **semanal** (cada um opcional, em tokens), registro de uso e **painel de administração** em `/admin`. Chave do dono sem limite. Passa os pedidos para a fila |
| **`llm-queue`** (`queue/`) | VPS, só rede interna | Fila e controlador da GPU: liga a máquina na primeira requisição, escolhe o host na Vast, abre o túnel SSH, desliga depois de 30 min ociosa, aplica os tetos de gasto, avisa no stream que a GPU está ligando |
| **GPU** | Vast.ai, 2×RTX 3090 | vLLM 0.30 com o modelo `twolven/Qwen3.8-27B-abliterated-AWQ-MTP` (AWQ 4 bits, contexto de 64k, tensor-parallel 2, ferramentas `qwen3_xml`, raciocínio `qwen3`) e, opcionalmente, o `Qwen3-Embedding-4B` (768 dimensões) |
| **Imagem da GPU** (`gpu-image/`) | GitHub Container Registry | Versão enxuta da imagem `vastai/vllm` (sem os kernels de GPUs Blackwell que a 3090 nunca usa e com camadas menores), para o cold start ser mais curto |

O gateway multiprovedor do LUAR (fork do 9Router) **não** faz parte deste repositório: ele chama este sistema como um provedor entre vários.

### Limites de valor

Preço total (máquina + disco) **nunca acima de US$ 0,45/h**, teto diário US$ 12, mensal US$ 80, desperdício por acordada US$ 0,25, saldo mínimo
configurável (`GPU_MIN_CREDIT_USD`). Se não há oferta abaixo do teto, o pedido recebe erro claro; o preço nunca é relaxado. Dentro da GPU, um vigia
independente para a própria instância depois de 45 min sem atividade, mesmo que o `llm-queue` tenha morrido.

## Como uma requisição percorre o sistema

```
cliente da API ──▶ llm-access (público, chave + limites) ──▶ llm-queue (fila + controlador) ── túnel SSH ──▶ vLLM na Vast
LUAR ─▶ gateway 9Router ─▶ llm-access (chave "platform") ───┘
kernel / Hermes ─▶ LiteLLM ─▶ gateway ─▶ llm-access ...        (e o kernel consulta /v1/gpu/status para avisar o usuário)
```

**GPU desligada.** O cabeçalho 200 sai na hora. Em stream, a primeira linha é uma mensagem fixa em português ("o computador da IA estava desligado
e está sendo ligado agora, leva de X a Y minutos e não é erro"), repetida com a etapa e a estimativa a cada minuto, e fechada com "pronto". Ela vai como
`delta.reasoning` (os dois nomes que o vLLM usa), então aparece como "pensando" e **não entra no histórico da conversa**; um campo `x_queue` traz
estado e estimativa para quem quiser desenhar uma barra. Com o cabeçalho `X-Queue-Notice: content` o aviso vai como texto da resposta, e com `X-Queue-Wait: 0` o pedido
acorda a GPU e volta na hora com `202` e `Retry-After`. Em respostas JSON, espaços a cada 15 s mantêm a conexão viva. O `kernel-harness` do LUAR pausa os relógios de
silêncio e de turno enquanto a fila estiver ligando e publica o mesmo aviso como evento `thinking`.

**Limites por pessoa** (regras copiadas do Claude Code): sessão de 5 h abre no primeiro pedido e reabre 5 h depois; semana de 7 dias; os dois relógios são independentes;
estourar qualquer um devolve `429` com `Retry-After` e a hora de reabertura; o saldo é um só por chave; `GET /v1/usage` mostra uso e reabertura; pedido que falha antes de
qualquer token real não abre janela nem cobra. Detalhes e contrato: [`access/DESIGN.md`](access/DESIGN.md).

## Como rodar e testar

Nada abaixo gasta dinheiro nem precisa de GPU.

```bash
cd queue  && pip install -r requirements-dev.txt && python -m pytest -q     # 68 testes: Vast e GPU simuladas, ofertas reais de 30/10 em tests/data
cd access && pip install -r requirements-dev.txt && python -m pytest -q     # 36 testes: janelas, concorrência, stream cortado, painel (upstream falso)
cd access/panel && npm ci && VITE_MOCK=1 npm run dev                        # painel com dados de mentira em http://localhost:5175/admin/ (senha: admin)
```

O QA dos limites usa janelas curtas (`ACCESS_QA_MODE=1` libera 1–2 minutos) e limites de 100 e 200 tokens, para ver o bloqueio e a reabertura sem esperar horas.

## Release e deploy

Uma tag `v*` gera, no mesmo release, a imagem e o manifesto de cada serviço (`release-manifest.llm-queue.json`, `release-manifest.llm-access.json`).
O `vps_rt_infra` descobre a release (`release-intake`), grava o `.lock` e implanta com `deploy.yml` (checagem de saúde e *rollback* automático). Segredos de produção ficam na VPS e no cofre, nunca aqui.
A imagem da GPU é publicada por `gpu-image.yml` (workflow manual), que **prova** que a imagem nova é a base menos os kernels removidos, comparando a árvore de arquivos inteira.
Operação do dia a dia e falhas conhecidas: [`docs/OPERACAO.md`](docs/OPERACAO.md).

## Benchmarks e por que esta configuração

A campanha de 26 a 28/10/2026 mediu o 27B e o 9B em RTX 3090, 4090, 5090, L40S, A100, H100 NVL, RTX PRO 6000 e GB10 (e visão, voz, imagem e vídeo em
sandbox). Resumo do que decidiu a configuração; tabelas completas, com a causa de cada "não carrega", em [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

- **O 27B em precisão alta (FP8, NVFP4, BF16) não cabe em placas de 24 ou 32 GB** (falta de memória, medido). Só o AWQ de 4 bits (≈18 GiB) cabe em uma 3090: 46,7 tok/s por usuário.
- **A RTX PRO 6000 (96 GB) é a melhor para tráfego contínuo**: NVFP4 + decodificação especulativa dá 143 tok/s com 1 usuário e 32 usuários dentro do SLA, o melhor custo por token entre as que carregam o NVFP4. Mas custa ~US$ 1,40/h.
- **A GB10 foi apagada**: banda de memória baixa (26,7 tok/s no melhor caso), serve para estudo, não para produto.
- **Nosso tráfego é intermitente**, então o que importa é o custo *por uso*: 2×3090 (≈ US$ 0,42/h) ligadas só quando há pedido cabem em US$ 80/mês, e a segunda placa compra memória para contexto de 64k e KV.
- **O preço do intermitente é o cold start**: 6,3 min no melhor caso, 9–12 min com host novo, 18 min no pior caso observado; religar a instância guardada leva ~108 s. Por isso o aviso no stream e o estudo do cold start (imagem enxuta, pesos no Drive).
- Limitações honestas: o AWQ muda o comportamento da abliteração em relação ao NVFP4; só há ~4 ofertas de 2×3090 abaixo de US$ 0,45/h; H100 NVL tem células só estimadas.

## O que foi testado

| Camada | O que prova | Custo |
|---|---|---|
| `queue/tests` (68) | máquina de estados da GPU, tetos, hosts ruins, aviso no stream, 202, log de ciclos, adoção após reinício; Vast simulada com ofertas reais | zero |
| `access/tests` (36) | janelas de 5 h e semanal, reserva atômica, concorrência, stream cortado, reinício no meio da janela, painel, chaves | zero |
| Vast real (30/10/2026) | cold start do zero (446 s e pior caso 1.088 s), religar (108 s), parada por ociosidade, chamada pelo gateway com keepalive, Hermes pelo kernel-harness | ≈ US$ 0,57 |
| Acordada completa na Vast (30/10) | GPU do zero até READY em 20,4 min (imagem 818 s, pesos pelo Drive 151 s, vLLM 251 s); Hermes concluiu; 22 avisos no stream; limites de 5 h e semanal bloqueando com a hora de reabertura; religar em 125 s. Detalhes e as tentativas que falharam: `studies/coldstart/` e Spec 14 §18 | ≈ US$ 0,47 |
| Campanha de benchmarks (26–28/10) | matriz modelo × GPU, capacidade por GPU, contexto longo, qualidade da quantização | repo privado `benchmarks` |
| Estudo do GB10 (jul–out/2026) | velocidade, qualidade (HumanEval 96,3 %, GSM8K 96,0 %), sonda de recusa, cache de KV | [`docs/GB10_STUDY.md`](docs/GB10_STUDY.md) |

## Mapa do repositório

```
queue/        serviço llm-queue (FastAPI): fila, controlador da GPU, aviso no stream, ciclos, bootstrap da instância (remote/)
access/       serviço llm-access (FastAPI + SQLite) e panel/ (React + Tailwind): chaves, janelas, painel
gpu-image/    gerador do Dockerfile da imagem enxuta da GPU
docs/         BENCHMARKS, OPERACAO, DECISOES e o estudo original do GB10
legacy/gb10/  Terraform, Traefik e scripts da GB10 apagada (histórico)
tests/ reports/   scripts e dados brutos dos benchmarks do GB10
.github/      CI, release (um release, dois artefatos), inspeção e build da imagem da GPU
```

## Segurança

Repositório público: sem segredos, sem endereços privados, ofertas de teste sanitizadas (`queue/tests/data`). O painel exige senha e HTTPS, o `llm-queue` não tem porta publicada, chaves ficam
só como SHA-256 e nenhum texto de mensagem é gravado (só tokens e metadados). Saídas de modelo abliterado nunca entram no repositório, só agregados.
