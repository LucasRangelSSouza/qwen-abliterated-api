# Benchmarks: o que foi medido em cada GPU e por que a produção é 2×RTX 3090 sob demanda

Fonte: campanha de 26 a 28/10/2026 (dados brutos no repositório privado `benchmarks`, porque contêm gerações de modelo abliterado) e o estudo do GB10
([`GB10_STUDY.md`](GB10_STUDY.md)). Cada célula tem um rótulo: **medido** (rodou na GPU), **não carrega** (medido: tentou e falhou, com a causa),
**estimativa** (não medido; não use como fato). Preços de marketplace variam por hora: para decidir compra, recalcule com a oferta do dia.

SLA usado: primeiro token ≤ 3 s em prompt curto e ≥ 20 tok/s por usuário.

## 1. Qwen3.8-27B abliterado por GPU (1 placa, exceto onde dito)

`tok/s (1 usuário)` / `pico agregado @ usuários` / `usuários dentro do SLA`.

| Configuração | RTX 3090 24 GB | RTX 4090 24 GB | RTX 5090 32 GB | L40S 48 GB | H100 NVL 94 GB | RTX PRO 6000 96 GB | GB10 121 GB |
|---|---|---|---|---|---|---|---|
| NVFP4 + DFlash2 | não carrega (memória) | não carrega | não carrega (checkpoint de 28,2 GiB, memória) | 104,1 / 246 @32 / 1 | estimativa | **143,1 / 1.466 @32 / 32** | 25,5 / 221 @32 / 1 |
| FP8 + DFlash2 | não carrega | não carrega | não carrega | 109,7 / 210 @32 / 4 | 118,0 / 1.402 @32 / 8 | **173,5 / 1.039 @32 / 32** | 26,7 / 215 @32 / 1 |
| FP8 (sem DFlash2) | não carrega | não carrega | não carrega | 21,7 / 338 @32 / 4 | estimativa | 45,4 / 694 @32 / 32 | 8,0 / 122 @32 / **0 (não cumpre)** |
| BF16 + DFlash2 | não carrega | não carrega | não carrega | não carrega | 87,2 / 748 @64 / não cumpre | 78,7 / 608 @16 / 16 | 13,1 / 125 @32 / não cumpre |
| **AWQ 4 bits (Huihui, 18 GiB)** | **46,7 / 257 @16 / 8** | 53,7 / 296 @16 / 8 | 83,6 / 844 @16 / 16 | 43,2 / 216 @8 / 8 | estimativa | 76,7 / 693 @16 / 16 | 13,6 / 116 @16 / não cumpre |
| Qwen3.5-9B BF16 (referência) | 48,2 / 544 @32 / 16 | 56,9 / 710 @32 / 16 | 95,5 / 1.802 @32 / 32 | 50,5 / 559 @32 / 16 | estimativa | 84,6 / 967 @32 / 16 | 12,9 / 132 @16 / não cumpre |

Causas dos "não carrega" (medido): o vLLM aborta na inicialização por **falta de memória** (`torch.OutOfMemoryError`); NVFP4 exige GPU Blackwell
(5090, PRO 6000, GB10); na L40S, FP8 + DFlash2 só cabe com contexto curto (faltaram 1,1 GiB de KV com 32k); o MTP do AWQ não subiu no vLLM 0.30.

## 2. Capacidade e custo (A100 incluída)

| GPU | configuração | usuários no SLA | tok/s agregado | US$/h | US$/M tokens (70 % de uso) |
|---|---|---|---|---|---|
| RTX 5090 | 9B BF16 | 32 | 1.802 | 0,60 | 0,132 |
| RTX 3090 | 9B BF16 | 16 | 493 | 0,25 | 0,201 |
| RTX 5090 | 27B AWQ | 16 | 844 | 0,60 | 0,282 |
| RTX PRO 6000 | 27B NVFP4 + DFlash2 | 32 | 1.466 | 1,40 | 0,379 |
| RTX 3090 | 27B AWQ | 8 | 224 | 0,25 | 0,443 |
| RTX PRO 6000 | 27B FP8 + DFlash2 | 32 | 1.039 | 1,40 | 0,535 |
| RTX 4090 | 27B AWQ | 8 | 283 | 0,50 | 0,702 |
| A100 PCIe | 27B AWQ | 8 | 344 | 0,67 | 0,773 |
| H100 NVL | 27B FP8 + DFlash2 | 8 | 823 | 2,90 | 1,398 |
| L40S | 27B AWQ | 8 | 216 | 0,80 | 1,468 |

Custo por token **só vale com a GPU ocupada**. Com tráfego intermitente manda o custo por hora ligada e o número de horas ligadas.

## 3. A configuração de produção: 2×RTX 3090, AWQ, 64k, sob demanda

Medida à parte (Spec 13, Vast real, 30/10/2026), vLLM 0.30, tensor-parallel 2, `--max-model-len 65536`, cache de prefixo ligado, estado Mamba em bf16:

| Medida | Resultado |
|---|---|
| Fila FIFO de 20 clientes, cache quente | 43 s (linha de base sem otimização: 150 s); 15 s para os 20 é impossível (limite físico ≥ 27 s) |
| Capacidade em 15 s | ~7 requisições quentes, ~3 frias |
| Qualidade (sonda de 60 itens) | 60/60 em bf16 e em fp32 |
| Cold start do zero, host com imagem em cache | 6,3 min (446 s com troca automática de um host com erro de GPU) |
| **Cold start do zero com a imagem enxuta e pesos pelo Drive (30/10, Bulgária)** | **20,4 min**: imagem 818 s, download 151 s (~130 MB/s), vLLM 251 s, aquecimento 4 s; o gargalo é a imagem no host |
| Religar a instância parada (mesma acordada) | 125 s |
| Drive contra Hugging Face (mesma máquina, 2 Gbps) | 102 MB/s contra 56 MB/s em um fluxo |
| Cold start, host novo | 9 a 12 min; pior caso observado 18 min (host gastou 8 min baixando a imagem); com a imagem de 23,7 GB da base, 2 de 3 hosts de teste não terminaram o pull em 14 min |
| Religar a instância parada | ~108 s |
| Preço | US$ 0,4236/h (Bulgária) a ~0,356 (Quebec); disco parado US$ 0,013 a 0,036/h |
| Ofertas 2×3090 abaixo de US$ 0,45/h | só ~4 de 51 passam nos filtros |

## 4. Por que 2×3090 sob demanda

1. **Cabe e é a mais barata que cabe.** Em placa única de 24 GB só o AWQ entra; a segunda placa compra memória para contexto de 64k, cache de KV e várias requisições.
2. **O tráfego é intermitente.** Uma PRO 6000 (US$ 1,40/h) ganha em custo por token com demanda contínua, mas ligada só quando há pedido a 3090 dupla (≈ US$ 0,42/h) cabe no teto de US$ 80/mês.
3. **O preço é o cold start**, e por isso existem o aviso no stream, o estudo da imagem enxuta (a imagem base tem 23,7 GB descompactados e 6,2 GB deles são kernels de GPUs Blackwell
   que a 3090 nunca carrega) e os pesos espelhados no Google Drive.

## 5. O que **não** foi medido

H100 NVL: 13 células só estimadas (excluída por custo). Comportamento do AWQ com auditoria humana contra o NVFP4 (o cumprimento de pedidos de limite muda entre abliterações:
~81 % no AWQ base Colletti, ~24 % no NVFP4 Blackfrost, ~43 % em FP8/BF16). Soak de 24 h com a imagem enxuta. Embedding convivendo com a LLM (medição no estudo de cold start, ver `DECISOES.md`).
