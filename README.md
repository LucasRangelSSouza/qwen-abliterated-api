# luar_llm_queue

Fila de IA com **GPU sob demanda** para o LUAR e os demais negócios (Spec 13, `new_ragente/specs/13_FILA_LLM_GPU_SOB_DEMANDA.md`).
API compatível com OpenAI (`/v1/chat/completions`, `/v1/embeddings`) na frente de 2×RTX 3090 alugadas na Vast.ai. **Nada fica ligado
24/7**: a GPU liga na primeira requisição, desliga depois de `GPU_IDLE_MIN` (30) minutos sem uso, e a instância parada é destruída
depois de `GPU_STOPPED_TTL_H` (24) horas.

```
LUAR ─▶ llm-gateway ─┐
portfólio / RAG ─────┼▶ llm-queue (esta repo, Contabo) ─ túnel SSH ─▶ Vast 2×3090: vLLM (LLM) [+ embedding]
outros negócios ─────┘     fila · controlador da GPU · tetos de gasto
```

## Endpoints
| Rota | O que faz |
|---|---|
| `POST /v1/chat/completions` | chat (stream ou não). Cabeçalhos saem na hora; durante o cold start vão `: acordando` (stream) ou espaços (JSON) a cada 15 s |
| `POST /v1/embeddings` | `qwen-embedding`, 768 dimensões. Exige `GPU_EMBED_MODE=gpu` (padrão `off` até o teste de convivência com a LLM, Spec 13 §4.2) |
| `GET /v1/models` | modelos servidos |
| `GET /v1/gpu/status` | `{state, eta_s, queue, spend{month,day,cap,remaining}}` |
| `GET /metrics`, `GET /health` | Prometheus e saúde |

Autenticação: `Authorization: Bearer <chave>`; consumidores em `CONSUMERS_JSON` (`[{"name","key_sha256","priority"}]`). Classes de tráfego:
`X-Queue-Class: interactive` (padrão) ou `batch` (só com `GPU_BATCH_ENABLED=true`).

## Estados da GPU
`OFF → STARTING → (FALLBACK) → LOADING → READY → STOPPING → OFF`. Religar a instância parada leva ~3 min; do zero 6 a 12 min (medido).
Host indisponível (Spec 13 §5.3): religar sem GPU livre cai no fallback; oferta que sumiu passa para a próxima; host com erro de GPU ou de
registro de imagem é destruído na hora e marcado como ruim; host que cai em produção é recriado; sem oferta abaixo do teto de preço, `503`.

## Limites de valor (variáveis, padrões da Spec 13 §8.1)
`GPU_MAX_ALLIN_DPH_USD=0.45` · `GPU_MAX_DAILY_USD=12` · `GPU_MONTHLY_BUDGET_USD=80` · `GPU_MIN_CREDIT_USD=3` · `GPU_MAX_WASTE_PER_WAKE_USD=0.25` ·
`GPU_MAX_SESSION_H=8`. O preço nunca é relaxado. Segurança extra: `remote/watchdog.sh` dentro da GPU a para sozinha após 45 min sem atividade,
mesmo que este serviço morra.

## Configuração mínima (env)
`REDIS_URL` · `VAST_API_KEY` · `SSH_PRIVATE_KEY_B64` (chave SSH que a conta Vast conhece, em base64) · `CONSUMERS_JSON` ·
opcionais: `GOOGLE_CHAT_WEBHOOK`, `GPU_*`. `REDIS_URL=fakeredis://` só para desenvolvimento local.

## Desenvolvimento
```
pip install -r requirements-dev.txt
python -m pytest -q          # 45 testes, sem gastar nada (Vast e GPU simuladas, ofertas reais de 30/10 em tests/data)
bash scripts/verify.sh       # testes + a imagem sobe e responde /health
```
O controlador está em `app/gpu/controller.py`; a qualificação de hosts (função pura, testada com 51 ofertas reais) em `app/gpu/hosts.py`;
o script que roda na instância em `remote/bootstrap.sh` (com o vigia embutido).

## Deploy
Release por tag `v*` (workflow `release.yml`: gate, imagem, manifesto). Quem implanta é o `vps_rt_infra` (intake gera o `.lock`, `deploy.yml`
implanta). Stack em `deploy/compose.yml`: o alias de rede `llm-queue.rt` existe porque o anti-SSRF do llm-gateway recusa hostname sem ponto.

## Operação
- Estado: `curl -H "Authorization: Bearer <chave>" http://llm-queue:8000/v1/gpu/status`.
- Parada de emergência: `docker stop llm-queue` **não** desliga a GPU; use o painel da Vast ou espere o vigia (≤ 45 min). Gasto do mês em `/v1/gpu/status`.
- Hosts ruins ficam em Redis (`llmq:bad_hosts`, 24 h a 7 dias); hosts bons em `llmq:known_good` (sobem em ~50 s por já terem a imagem).
