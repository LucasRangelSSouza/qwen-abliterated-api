# Especificação operacional — Qwen Abliterated API

## Objetivo

Operar uma API compatível com OpenAI para um Qwen3.8-27B abliterated (thinking, código, tool calling, visão) em uma GPU alugada, publicada em `https://qwen.example.com/v1`, reconstruível em outra máquina mudando apenas o endereço dela, e sem redownload dos pesos em reinícios normais.

## Contrato de acesso

- SSH real na instância (`VAST_TCP_PORT_22`), com chave própria de deploy. O Jupyter/navegador não é caminho de operação.
- A chave pública é cadastrada **na conta e na instância** pela API da Vast (a Vast reescreve `authorized_keys` a partir das chaves da conta; cadastrar só na instância não persiste). A privada fica nos secrets do GitHub e no cofre privado (`personal-skills/secrets/qwen-api.env`).
- Secrets do repositório: `DEPLOY_SSH_PRIVATE_KEY`, `VLLM_API_KEY`, `HOSTINGER_API_KEY`, `EDGE_SSH_PRIVATE_KEY`, `VAST_API_KEY`, `VAST_INSTANCE_ID`.
- Trocar de máquina = informar novo IP, porta SSH, porta da API e id da instância no workflow `deploy-vast.yml` ou nas variáveis do Terraform.

## Persistência e ciclos de energia

- Pesos em `/root/models` (disco local da instância): BF16 do checkpoint (52 GB) + drafter (3,6 GB). Sobrevivem a stop/start; `PRUNE_UNUSED=1` remove pesos do perfil não usado (disco cobrado não guarda arquivo sem uso).
- Reiniciar ou parar/iniciar nunca dispara download: `hf download` só move bytes se faltar arquivo. **Medido**: `tests/idempotency.py`.
- Recriar/destruir não é reiniciar: o bootstrap baixa de novo (~10 min para 56 GB). Nunca destruir sem pedido explícito.
- Ligar/desligar por API: `scripts/vast-power.sh start|stop|status` (chave de conta). Parada cobra só disco (~US$ 0,007/h).

## Infraestrutura reproduzível

```text
Terraform (infra/terraform-vast)          GitHub Actions (deploy-vast.yml)
---------------------------------         -------------------------------
configure  -> pesos + vLLM na instância   mesmas etapas, disparo manual
publish    -> rota Traefik + cookie Vast  inputs: ssh_host, ssh_port, api_port,
dns        -> registro A na Hostinger              instance_id, profile (fp8|nvfp4)
verify     -> completion real pela URL
```

- Cada etapa do Terraform re-executa só quando suas entradas mudam (`triggers_replace`), e cada script é idempotente: `terraform plan` após `apply` mostra *No changes* (verificado) e um segundo `apply` não altera nada.
- `scripts/configure-vast-vllm.sh`: bloco gerenciado em `/workspace/.env`; só reinicia o vLLM se o bloco difere do desejado; espera até 15 min pelo health check antes de considerar que falhou (não reinicia um servidor que ainda está carregando).
- `scripts/publish-endpoint.sh`: reescreve exatamente 3 blocos gerenciados no Traefik (router, middleware, service); segunda execução responde *unchanged*.
- `scripts/dns-upsert.sh`: toca apenas o registro nomeado (`overwrite=false`).
- Caminho alternativo para uma VM Ubuntu genuína (Docker + Caddy): `compose.yaml` + `infra/terraform` + `deploy.yml`.

## API

- Endpoint: `POST /v1/chat/completions`, `GET /v1/models` (também Responses API do vLLM).
- Modelo servido: `qwen-abliterated`. Contexto configurado: 160 000 tokens (cache de KV em bf16; fp8 corrompe contextos longos).
- Autenticação: `Authorization: Bearer <VLLM_API_KEY>` (chave estável, imposta pelo vLLM). A borda injeta o cookie de autenticação da Vast, que muda a cada instância e nunca chega ao cliente.
- Thinking: ligado por padrão quando o cliente não diz nada; `chat_template_kwargs.enable_thinking=false` desliga. O raciocínio vem em `reasoning`, a resposta em `content`.
- Visão: `image_url` (URL ou data URI). Áudio não é modalidade do modelo: a API rejeita `input_audio` com 400 e o caminho suportado é fala → texto (whisper) → API (`tests/pipeline_audio.py`).
- Aceite (todos automatizados em `tests/`): auth 401/401/200; completion real; thinking on/off; tool calling; imagem; áudio rejeitado sem derrubar o servidor; 16 perguntas paralelas com respostas corretas; recuperação de informação em prompts de até ~140k tokens; HumanEval e GSM8K; estabilidade em 60 requisições; idempotência (re-execução, reparo de configuração, restarts forçados, stop/start real pela API).

## Modelo e decisão de quantização

A GB10 (~273 GB/s) tem decode limitado por banda de memória; a decodificação especulativa (drafter DFlash2, 7 tokens) muda o gargalo. Decisão por medição (detalhes em `docs/RESULTS.md`):

| | qualidade (HumanEval / GSM8K) | prefill 140k | vazão agregada 4 clientes |
|---|---|---:|---:|
| **FP8 (padrão)** | 96,3 % / 96,0 % | ~100 s | ~99 tok/s |
| NVFP4 | 93,9 % / 97,0 % | ~121 s | ~78 tok/s |

A diferença de qualidade entre os dois não é estatisticamente significativa (McNemar p ≥ 0,125); FP8 vence em prefill e vazão. Régua: Sonnet 5 medium, 100 % / 98,5 % no mesmo avaliador.

- Target padrão: `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16` quantizado para FP8 pelo vLLM no carregamento; alternativa `PROFILE=nvfp4` (`...-NVFP4`).
- Drafter: `z-lab/Qwen3.8-27B-DFlash2`.
- GGUF (Q4/Q6/Q8) não é opção neste runtime: vLLM 0.30 rejeita GGUF.

## Limites conhecidos

- Reinício completo (kill → API pronta) ≈ 7 min no FP8 (52 GB lidos do disco, mais quantização); NVFP4 ≈ 4,5 min.
- Prosa livre ≈ 14 tok/s (o drafter acerta menos em texto imprevisível); código e SQL ≈ 35-38 tok/s.
- Prompts longos custam prefill: ~12 s com 30k tokens, ~100 s com 140k (o prefix cache reduz repetições para ~2 s).
- Uma carga de GPU em tempo real ao lado (por exemplo vídeo) divide a mesma banda de memória e reduz a vazão do LLM; `GPU_UTIL` deixa memória livre, mas não banda.
- Não é equivalente a um modelo de fronteira em código agêntico (ver régua).
