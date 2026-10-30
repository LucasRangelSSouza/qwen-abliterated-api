# Decisões (com data)

| Data | Decisão | Motivo |
|---|---|---|
| 28/10/2026 | Modelo AWQ 4 bits (`twolven/Qwen3.8-27B-abliterated-AWQ-MTP`), contexto 64k | Único formato que cabe em placas de 24 GB; ver `BENCHMARKS.md` |
| 29/10/2026 | 2×RTX 3090 na Vast, nunca 24/7 | Tráfego intermitente; preço por hora ligada |
| 30/10/2026 | Teto de US$ 0,45/h (hardware + disco), nunca relaxado | Orçamento mensal; só ~4 ofertas passam |
| 30/10/2026 | Ociosidade de 30 min para desligar | Pedido do dono; cada acordada custa ~US$ 0,25–0,30 |
| 30/10/2026 | GB10 apagada | Banda de memória baixa; o projeto novo é a fila sob demanda |
| 30/10/2026 | Fila (`llm-queue`) entra neste repositório, com histórico, e sai junto com o `llm-access` num release | Mexer em um é mexer no outro |
| 30/10/2026 | `llm-access` público na frente da fila; fila só em rede interna | A fila tem rotas de controle da GPU |
| 30/10/2026 | Janelas de 5 h e semanal copiam o Claude Code; banco SQLite em volume | Um administrador, um processo |
| 30/10/2026 | Aviso de espera como raciocínio, não como resposta | Não suja o histórico da conversa |
| 30/10/2026 | Imagem enxuta da GPU no GHCR e pesos no Drive (Hugging Face de reserva) | Encurtar o cold start; a imagem base é a parte mais lenta |
| 30/10/2026 | Risco de credencial em máquina de terceiro aceito | Nada sigiloso na conta; tudo se recria |
| 30/10/2026 | Pesos pelo Drive como origem principal, Hugging Face de reserva e limite de 20 min no Drive | Medido: Drive 102 MB/s contra 56 MB/s do Hugging Face (um fluxo, mesma máquina) |
| 30/10/2026 | Embedding fica desligado em produção (`GPU_EMBED_MODE=off`) até o teste dedicado | Com LLM + embedding a subida passa de ~25 min (limite de espera) |
| 30/10/2026 | `GPU_RUNNING_TIMEOUT_S` 480 → 900 e troca de oferta no mesmo ciclo (`GPU_CONNECT_RETRIES=1`) | A imagem leva 6 a 11 min para puxar e hosts sem SSH derrubavam o pedido |
