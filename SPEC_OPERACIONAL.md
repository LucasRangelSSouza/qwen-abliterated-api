# Especificação operacional — Qwen Abliterated API

## Objetivo

Operar uma API compatível com OpenAI para um Qwen abliterated em uma GPU alugada, com reconstrução automatizada em outra máquina e sem redownload dos pesos em reinícios normais.

## Contrato de acesso

- A máquina definitiva deve ser uma VM com `sshd` funcional, não apenas um container de template.
- A chave pública de deploy é cadastrada na VM no provisionamento. A chave privada correspondente fica exclusivamente no secret `DEPLOY_SSH_PRIVATE_KEY` do GitHub e no operador autorizado.
- O workflow GitHub Actions usa `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER` e `DEPLOY_SSH_PRIVATE_KEY`; trocar de provedor/máquina consiste em atualizar esses secrets e rodar o deploy.
- O acesso Jupyter é contingência para containers Vast que anunciam SSH, mas não entregam `sshd`; ele não satisfaz o contrato de produção.

## Persistência e ciclos de energia

- Pesos GGUF e caches ficam em volume/disco persistente, separado do root efêmero quando o provedor disponibilizar volume. Nesta instância de transição, o arquivo está em `/root/model-cache` no disco local de 80 GB e sobrevive a stop/start da instância Vast.
- Reiniciar ou parar/iniciar não pode disparar `hf download` se o arquivo já passou na verificação de tamanho/hash.
- Recriar/destruir não é reiniciar: o bootstrap deve baixar novamente a partir do repositório de modelo, usando o cache/volume anexado quando existir.

## Infraestrutura reproduzível

```text
Terraform                 GitHub Actions                    VM
---------                 --------------                    --
alocação/rede/volume  ->  SSH + bootstrap idempotente   ->  Docker + NVIDIA
secrets de conexão     ->  compose pull/up               ->  vLLM + Caddy
```

- Terraform é responsável por instância, disco/volume, IP/rede e outputs de conexão.
- `scripts/bootstrap-vm.sh` instala pré-requisitos de forma idempotente.
- `scripts/deploy-vm.sh` sincroniza a stack e executa Compose de forma idempotente.
- Aplicação e configuração residem em `compose.yaml`; nenhum procedimento manual é requisito para uma VM com SSH funcional.
- O container vLLM do Vast é um perfil transitório. Ele usa Supervisor e `/workspace/.env`; o script `scripts/configure-vast-vllm.sh` documenta essa adaptação, mas produção deve usar a VM/Compose acima.

## API

- Endpoint: `POST /v1/chat/completions` e `GET /v1/models`.
- Nome servido: `qwen-abliterated`.
- Autenticação: bearer token configurado por secret (`VLLM_API_KEY`), nunca embutido no repositório.
- Aceite: `/v1/models` anuncia `qwen-abliterated`; uma completion retorna conteúdo; benchmark registra TTFT, tokens/s e código HTTP; a mesma verificação passa após stop/start.

## Modelos e decisão de quantização

| Variante | Tamanho aproximado | Uso recomendado na GB10 119 GB |
|---|---:|---|
| Q4_K_M | 16 GB | somente em runtime com loader GGUF |
| Q5_K_M | 19–21 GB | somente em runtime com loader GGUF |
| Q6_K | 22–25 GB | somente em runtime com loader GGUF |
| BF16 nativo | ~56 GB | perfil selecionado; máxima fidelidade e suportado pelo vLLM atual |

A GB10 de 119 GB comporta Q6 e BF16 confortavelmente para contexto de 16k. Nesta imagem Vast, vLLM 0.30 rejeita GGUF, portanto a promoção correta é o checkpoint BF16 nativo. Se outro runtime com GGUF for adotado, Q6 é a primeira variante a comparar; nenhum arquivo GGUF fica armazenado sem ser utilizável.

## Sequência de entrega

1. Corrigir e validar a API Q4 na instância de transição.
2. Validar SSH real em uma VM compatível e tornar o workflow de deploy o caminho principal.
3. Rodar benchmark comparativo Q4/Q6 (e Q8 se houver margem), registrar decisão.
4. Validar stop/start e tempo até `GET /v1/models` responder, sem novo download.
5. Parar — nunca destruir — a instância quando não estiver sendo usada.
