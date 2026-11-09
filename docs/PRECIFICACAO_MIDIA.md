# Precificação da mídia sob demanda (08/11/2026)

Premissa do Lucas: **econômica ao máximo**. Nada fica ligado 24 h: toda GPU de mídia é alugada na Vast só quando há pedido e destruída depois de uma espera curta. Câmbio usado: **US$ 1 = R$ 5,50** (open.er-api.com, 08/11/2026). Todos os valores são **preço de custo de GPU**; não incluem imposto, gateway de pagamento, suporte nem a LLM do chat.

## 1. De onde vêm os números

| Item | Valor | Classe |
|---|---|---|
| Máquina pequena (RTX 3090 Estônia, todo-incluído GPU + disco) | US$ 0,419/h (teto configurado: 0,65) | medido |
| Máquina grande (RTX PRO 6000, todo-incluído) | US$ 1,50/h | medido |
| Partida a frio da pequena (alugar + perfil imagem) | 300 s | medido (QA 08/11) |
| Instalar perfil rosto numa máquina pequena de pé | 154 s | medido |
| Instalar perfil voz numa máquina pequena | ~340 s | medido (QA 08/11) |
| Partida a frio da grande (alugar + perfil vídeo) | 400 a 600 s | medido |
| Espera antes de destruir | pequena 900 s, grande 600 s | configuração |
| Imagem Flux 1024 (máquina quente) | ~20 s | medido |
| Vídeo Wan 14B 480p, 5 s | 236 a 482 s (média usada: 350) | medido em 3 máquinas |
| Vídeo Wan 14B 720p, 5 s | 852 a 1145 s (usado: 1000) | medido em 2 máquinas |
| Troca de rosto 480p, 4,5 s (3090) | 319 s | medido |
| Clonar voz, 10 s de fala (modelo já carregado) | ~30 s (carregar 14 a 25 s + gerar) | medido (QA 08-09/11) |
| Trocar voz de um vídeo de 4 s | 32 a 56 s | medido |
| Dublar um vídeo de 4 s (EN para PT) | 23 a 45 s | medido |
| Os mesmos para arquivos de 30 s | extrapolação linear | estimado |

## 2. Custo por sessão (a parte que derruba a margem com pouco volume)

Uma **sessão** é o tempo em que a máquina existe: partida a frio + pedidos + espera final. Ela só existe quando há pedido, mas é paga inteira por quem a abriu.

| Classe | Partida + espera | Custo fixo da sessão |
|---|---|---|
| Pequena (imagem, voz, dublagem, rosto) | 420 s + 900 s | **US$ 0,154 = R$ 0,84** |
| Grande (vídeo Wan 14B) | 600 s + 600 s | **US$ 0,500 = R$ 2,75** |

Quanto mais pedidos na mesma sessão (N), menor o custo fixo por pedido: custo do pedido = custo da GPU + (custo fixo da sessão ÷ N). **Todos os pedidos da classe pequena dividem a mesma sessão** (uma imagem, uma troca de voz e uma troca de rosto seguidas usam a mesma máquina).

## 3. Custo por chamada

Valores em R$. `N` = pedidos na sessão.

| Chamada | GPU (s) | Custo na máquina quente | N = 1 | N = 4 | N = 20 |
|---|---:|---:|---:|---:|---:|
| Imagem Flux 1024 | 20 | 0,013 | 0,86 | 0,22 | 0,055 |
| Clonar voz (texto ~30 s) | 60 | 0,038 | 0,88 | 0,25 | 0,081 |
| Trocar voz (áudio 30 s) | 140 | 0,090 | 0,93 | 0,30 | 0,132 |
| Dublar vídeo 30 s | 250 | 0,160 | 1,01 | 0,37 | 0,202 |
| Troca de rosto 5 s, 480p | 319 | 0,204 | 1,05 | 0,42 | 0,246 |
| Vídeo Wan 480p, 5 s | 350 | 0,802 | 3,55 | 1,49 | 0,940 |
| Vídeo Wan 720p, 5 s | 1000 | 2,292 | 5,04 | 2,98 | 2,429 |

Conclusões:
- **Vídeo é o único item caro**: um Wan 720p custa ~R$ 2,30 só de GPU; imagem custa R$ 0,013.
- **O risco comercial é o N = 1** (cliente isolado que abre a máquina sozinho): a imagem sai a R$ 0,86. Com a base de clientes junta, o N real tende a 4 ou mais.
- Troca de rosto e voz são **baratas**: rodam na 3090 de US$ 0,42/h, não na PRO 6000.

## 4. Proposta de crédito

**1 crédito = R$ 0,20 de preço de tabela.** Tabela de preço sugerida (margem bruta de GPU em N = 1 / 4 / 20):

| Chamada | Créditos | Preço (R$) | Margem N=1 | N=4 | N=20 |
|---|---:|---:|---:|---:|---:|
| Imagem Flux | 3 | 0,60 | -43% | 63% | 91% |
| Clonar voz | 5 | 1,00 | 12% | 75% | 92% |
| Trocar voz (30 s) | 6 | 1,20 | 22% | 75% | 89% |
| Dublar vídeo (30 s) | 12 | 2,40 | 58% | 85% | 92% |
| Troca de rosto (5 s) | 12 | 2,40 | 56% | 83% | 90% |
| Vídeo Wan 480p (5 s) | 30 | 6,00 | 41% | 75% | 84% |
| Vídeo Wan 720p (5 s) | 70 | 14,00 | 64% | 79% | 83% |

Regras de arredondamento: duração maior que a unidade (vídeo acima de 5 s, áudio ou dublagem acima de 30 s) cobra proporcional, em múltiplos da unidade (vídeo: um Wan 480p por clipe de 5 s; troca de rosto: 12 créditos a cada 5 s).

Como ler a margem: é margem **sobre o custo de GPU**. Para o lucro real, desconte imposto, taxa de pagamento, suporte e a LLM do chat.

## 5. O que ainda é estimativa

Clonar voz, trocar voz e dublar foram **medidos só com arquivos curtos** (4 a 10 s); a tabela do §3 extrapola linearmente para 30 s. Refazer com `GET /v1/jobs/{id}` (`metrics`) e `seconds` quando houver pedidos reais maiores. Os tempos do Wan variam 2x entre máquinas (a Vast entrega GPUs diferentes sob o mesmo nome): por isso a tabela usa a média e o teto de preço da máquina (`WORKER_MAX_DPH_USD`, `WORKER_SMALL_MAX_DPH_USD`).

## 6. Decisões que mexem na conta

| Decisão | Efeito |
|---|---|
| Aumentar a espera antes de destruir (`WORKER_*_IDLE_S`) | Mais pedidos por sessão (N maior), porém mais custo fixo por sessão |
| Preço mínimo por pedido (piso) | Protege o N = 1 sem mexer na tabela |
| Franquia por plano em créditos em vez de "100 imagens" | Vídeo e imagem passam a consumir da mesma conta |
| Limite de vídeo 720p só nos planos Max e Ultra | Tira o item de maior risco do plano de entrada |

## 7. Cota de mídia por plano (regra: custo de GPU ≤ 1/3 da mensalidade)

Aplicada no llm-access (`app/mediaquota.py`, tabela `media_usage`): o pedido passa pela cota antes de ir ao llm-queue; acima dela volta HTTP 429 `media_quota_exceeded` com mensagem em português ("Você já usou a cota de vídeos do seu plano neste mês (X de Y). Ela renova em dd/mm."). Mês civil (UTC). Falha do servidor não gasta cota. Chaves do dono e da plataforma não têm cota. A cota **não aparece para o cliente** (só a mensagem de 429 quando estoura). Quem assina já nasce com a cota do plano, lida a cada chamada: mudou o plano, mudou para todos. No painel: Assinantes > Planos edita a cota de cada plano; Pessoas > Editar > "Créditos de mídia por mês" dá um valor diferente a uma pessoa (vazio volta a seguir o plano), pela API `PATCH /admin/api/people/<id>` com `image_quota_month`, `video_quota_month`, `audio_quota_month` (`follow_plan: true` limpa também os ajustes de mídia); o `GET /admin/api/people` mostra limite, uso e se é ajuste manual. `NULL` = plano sem cota configurada (não bloqueia).

Unidades: imagem = 1 (campo `n`); áudio (clonar voz, trocar voz) = 1; vídeo = clipes de 5 s em 480p, com o 720p contando **3** (custo medido 2,9x); dublagem = 1; troca de rosto = 1 por 5 s.

Orçamento: a mídia pode gastar no máximo **25% da mensalidade** no pior caso (todas as cotas usadas); os outros ~8 pontos até o teto de 1/3 ficam de folga para variação da Vast (Wan até 2x entre máquinas), taxa do Cakto e LLM. Premissas do pior caso: máquina pequena aberta em 10 (Lite), 25 (Max) e 30 (Ultra) sessões por mês a R$ 0,84; vídeos com 1, 2 e 3 pedidos por sessão da máquina grande (R$ 2,75 por sessão).

| Plano | Preço | Imagens | Vídeos (clipes 480p) | ou em 720p | Áudios | Custo no pior caso | % da mensalidade |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lite | R$ 119 | 100 | 5 | 1 clipe | 20 | R$ 29,3 | 24,6% |
| Max | R$ 497 | 1.000 | 30 | 10 clipes | 150 | R$ 112,8 | 22,7% |
| Ultra | R$ 1.197 | 4.000 | 100 | 33 clipes | 500 | R$ 294,1 | 24,6% |
| Enterprise | contrato | 10.000 | 300 | 100 clipes | 3.000 | definir no contrato | — |

Teto de 1/3 (R$ 39,7 / 165,7 / 399,0) respeitado com folga. O custo de GPU é o único variável por cliente; os R$ 208/mês das GPUs do chat são fixos e não entram na conta por plano. Subir uma cota é seguro até o teto de 1/3: no Max, por exemplo, cada vídeo extra de 480p custa ~R$ 2,2 no pior caso.
