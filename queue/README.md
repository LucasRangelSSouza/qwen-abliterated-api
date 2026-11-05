# luar_llm_queue

Fila de IA com **GPU sob demanda** para o LUAR e os demais negócios (Spec 13, `new_ragente/specs/13_FILA_LLM_GPU_SOB_DEMANDA.md`).
API compatível com OpenAI (`/v1/chat/completions`, `/v1/embeddings`) na frente de 2×RTX 3090 alugadas na Vast.ai. **Nada fica ligado
24/7**: a GPU liga na primeira requisição, desliga depois de `GPU_IDLE_MIN` (30) minutos sem uso, e a instância parada é destruída
depois de `GPU_STOPPED_TTL_H` (24) horas. Isso vale com o desligamento automático ligado; o padrão é desligado, e existe o modo de
instância fixa (`GPU_STATIC_INSTANCE_ID`), que nunca para nem destrói a máquina: ver `docs/OPERACAO.md`, seção "GPU fixa e interruptor".

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

## Progresso de imagem e vídeo (contrato do app)

`GET /v1/media/jobs/{id}` (mesma chave de quem pediu; outro consumidor recebe 404). Compatível com `GET /v1/jobs/{id}`, que segue devolvendo o registro bruto.

```json
{"id": "a1b2c3d4e5f6", "kind": "video", "status": "running", "position": null, "eta_s": 24, "progress": 42,
 "message": "Gerando o seu vídeo..."}
```

- `status`: `queued` | `running` | `done` | `failed`.
- `position`: posição na fila (1 = próximo) quando `queued`; `null` nos demais.
- `eta_s`: segundos estimados (na fila: o que falta do job em curso + os à frente + o próprio); `0` em `done`; `null` em `failed`.
- `progress`: 0 a 100. Real, vem do WebSocket do ComfyUI (`progress` passo/total): 3 = iniciou, 5 a 97 = amostragem, 100 só em `done`. Se o WebSocket cair, fica no último valor e o pedido segue.
- `message`: texto curto em português, sem infraestrutura: "Seu vídeo está na fila para ser gerado." / "Gerando o seu vídeo..." / "Pronto." (imagem: "Sua imagem..." / "Gerando a sua imagem..."). Em `failed`: a mensagem única de falha.
- Em `done` vêm também `result` (`/v1/jobs/{id}/file`), `mime`, `seconds`, `size`.
- Pedido que estava na fila quando o serviço reiniciou aparece como `failed`.
- Erros: `404 {"error": {"message": "Pedido não encontrado."}}`; `503` com "Este recurso não está disponível no momento." se a mídia estiver desligada.

Ordem: uma geração por vez na GPU de mídia. Imagem (`interactive`) passa na frente de vídeo na fila (`batch`); um vídeo em execução termina antes (limite de 4 s e 512x512). Pedido de vídeo: `POST /v1/videos/generations` (202 com o id); de imagem: `POST /v1/images/generations` (`"async": true` para 202).

### Parâmetros de imagem e vídeo (pedido e status)

`POST /v1/images/generations` e `POST /v1/videos/generations` (corpo JSON; `"async": true` devolve 202 com o id). Tudo é validado: número fora da faixa é **ajustado** (e listado em `adjusted`); texto de lista fechada inválido, tipo errado ou imagem ilegível recusa com 422 e mensagem em português.

| Parâmetro | Imagem (Flux schnell) | Vídeo (LTX 2B) |
|---|---|---|
| `prompt` | obrigatório, até 4000 caracteres | idem |
| `negative_prompt` | texto (só pesa com `cfg` > 1) | texto (padrão: qualidade ruim, tremido, texto, marca d'água) |
| `width`, `height` (ou `size: "1024x1024"`) | múltiplos de 64, 256 a 1536, até 1,65 MP; padrão 1024x1024 | múltiplos de 32, 256 a 832, até 512x512 de área (0,26 MP); padrão `format` |
| `format` | n/a | `paisagem` (640x384, padrão), `retrato` (384x640), `quadrado` (512x512) |
| `steps` | 1 a 12 (padrão 4) | 10 a 50 (padrão 30) |
| `cfg` (ou `guidance`) | 0,5 a 6 (padrão 1,0) | 1 a 8 (padrão 3,0) |
| `seed` | inteiro (padrão aleatório) | idem |
| `sampler` | `euler` (padrão), `euler_ancestral`, `heun`, `dpm_2`, `dpmpp_2m`, `dpmpp_2m_sde`, `dpmpp_sde`, `lms`, `lcm`, `uni_pc`, `ddim`, `res_multistep` | idem |
| `scheduler` | `simple` (padrão), `normal`, `karras`, `sgm_uniform`, `exponential`, `beta`, `ddim_uniform` | n/a |
| `n` (ou `quantidade`) | 1 a 4 imagens | n/a |
| `style` (ou `estilo`) | `foto`, `cinema`, `ilustracao`, `anime`, `3d`, `minimalista`, `aquarela` (acrescenta termos ao prompt) | n/a |
| `seconds` (ou `segundos`) | n/a | 1 a 4 (padrão 4); os quadros são 8n+1, então 4 s viram 3,88 s |
| `fps` | n/a | 8 a 30 (padrão 25), no máximo 121 quadros |
| `image` (ou `start_image`) | n/a | imagem inicial em base64 ou data URL (PNG, JPEG, WEBP, até 6 MB): vira imagem para vídeo |
| `strength` | n/a | 0 a 1 (padrão 1,0), só com `image`: menor dá mais liberdade de movimento |

`GET /v1/media/jobs/{id}` devolve, além do contrato de progresso: `type` (`"image"` ou `"video"`), `params` (valores **efetivamente usados**, com seed sorteada, tamanho final, `frames` etc.; nunca a imagem inicial), `adjusted` (nomes dos parâmetros que foram ajustados) e, em `done`, `mime`, `ext` e `results`:

```json
{"status": "done", "type": "video", "mime": "video/mp4", "ext": "mp4", "result": "/v1/jobs/ID/file",
 "results": [{"index": 0, "type": "video", "mime": "video/mp4", "ext": "mp4", "bytes": 122458, "url": "/v1/jobs/ID/file"}],
 "params": {"prompt": "...", "width": 640, "height": 384, "seconds": 2.0, "fps": 25, "frames": 49, "steps": 30, "cfg": 3.0, "seed": 123, "sampler": "euler", "image_to_video": false}}
```

Imagens com `n` > 1 trazem uma entrada em `results` por imagem (`/v1/jobs/ID/file?i=1`, `?i=2`...). O arquivo sai com o `Content-Type` do formato real (mp4 nunca como imagem); o servidor só entrega o arquivo do tipo pedido, nunca a prévia do ComfyUI.

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

## Estado da implantação (30/10/2026)
Em produção na VPS da Contabo (`v0.1.2`), atrás do `llm-gateway`: provedor `qq` (`http://llm-queue.rt:8000/v1`), combo `qwen-abliterated`, tenant `platform`.
Validado com a Vast real: cold start do zero 446 s (host com erro de GPU trocado sozinho), religar a instância guardada 108 s, parada por ociosidade,
resposta pelo gateway com os comentários SSE repassados. Pendente: embedding junto da LLM (`GPU_EMBED_MODE=off` até o teste da Spec 13 §4.2),
alerta no Google Chat (sem webhook configurado) e a troca do portfólio/RAG para este endpoint.

Pontos que custaram caro de aprender (detalhe na Spec 13 §9.3):
- A fila e as conexões dos clientes vivem na memória do processo; só gasto, hosts e instância ficam em Redis.
- O vLLM precisa de `--enable-auto-tool-choice --tool-call-parser qwen3_xml`: o Hermes manda `tool_choice: auto`.
- A Vast guarda o `onstart` na criação; por isso cada instância leva uma impressão (`spec`) do script e é destruída, não religada nem adotada, se ela mudar.
- Religar e parar devolvem HTTP 200 mesmo quando falham: sempre ler `success`/`error` do corpo (`resources_unavailable`).
- O TTL da instância parada é limitado também pelo custo do disco (`GPU_STOPPED_MAX_USD`).

## Operação
- Estado: `curl -H "Authorization: Bearer <chave>" http://llm-queue:8000/v1/gpu/status`.
- Parada de emergência: `docker stop llm-queue` **não** desliga a GPU; use o painel da Vast ou espere o vigia (≤ 45 min). Gasto do mês em `/v1/gpu/status`.
- Hosts ruins ficam em Redis (`llmq:bad_hosts`, 24 h a 7 dias); hosts bons em `llmq:known_good` (sobem em ~50 s por já terem a imagem).
