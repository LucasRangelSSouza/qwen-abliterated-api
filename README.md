# Qwen Abliterated API

Production-shaped, OpenAI-compatible vLLM deployment for `nasmtrcs/Qwen3.8-27B-OBLITERATED`. It exposes `/v1/models`, `/v1/chat/completions`, and the vLLM Responses API through HTTPS with a bearer key.

## Architecture

```text
Hostinger DNS A record -> Ubuntu GPU VM :443 -> Caddy TLS -> vLLM :8000 -> Qwen 3.8 27B
```

Terraform owns the replacement-safe machine configuration. Change `ssh_host` and `ssh_private_key_path`, then run `terraform apply`; it uploads the versioned deployment, bootstraps Docker/NVIDIA validation, starts the stack, and recreates the API.

## Prerequisites

- A genuine Ubuntu GPU VM with NVIDIA driver and Docker NVIDIA runtime available.
- At least 250 GB of instance disk. The selected 27B checkpoint and Hugging Face cache need significant headroom.
- A Hostinger A record for `api.example.com` pointing to the VM public IP, with ports 80 and 443 directly reachable.
- A private SSH key with a passwordless-sudo user.

The low-cost Vast offer is a **container**, not a full Ubuntu VM. It cannot run this Docker/Caddy topology. For that managed image use `scripts/configure-vast-vllm.sh`: it configures its built-in Supervisor/vLLM server without Docker-in-Docker. It places a Q4 GGUF model on the instance disk and uses a Vast local volume only for the persistent workspace configuration.

## First deploy

```bash
cp .env.example .env
# set API_DOMAIN and a random VLLM_API_KEY; never commit .env
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform apply -var-file=terraform.tfvars
```

`infra/terraform/terraform.tfvars.example` contains the required variables. Sensitive values still appear in Terraform state when provisioners are used: keep state local and encrypted, or configure a protected remote state backend before team use.

## DNS with Hostinger

Create an A record named `api` (or your selected hostname) pointing to the VM public IPv4. The Hostinger API authenticates using a bearer token created in hPanel → API. This repository deliberately does not embed that token or automatically mutate DNS; actions use only `DEPLOY_SSH_PRIVATE_KEY` and `VLLM_API_KEY`.

## API and client setup

```bash
export OPENAI_BASE_URL=https://api.example.com/v1
export OPENAI_API_KEY='your-vllm-key'
curl "$OPENAI_BASE_URL/models" -H "Authorization: Bearer $OPENAI_API_KEY"
```

```python
from openai import OpenAI
client = OpenAI(base_url="https://api.example.com/v1", api_key="your-vllm-key")
reply = client.chat.completions.create(
    model="qwen-abliterated",
    temperature=0,
    max_tokens=2048,
    messages=[{"role": "user", "content": "Write a robust Python retry helper."}],
)
print(reply.choices[0].message.content)
```

The selected model card recommends deterministic requests (`temperature=0`), `repetition_penalty=1.15`, at least 2048 new tokens for complex code, and thinking disabled because it can reintroduce refusal behavior. vLLM's Qwen reasoning parser remains enabled for clients that need the Responses API.

## Verification

```bash
scripts/smoke-test.sh https://api.example.com "$VLLM_API_KEY"
scripts/benchmark.sh https://api.example.com "$VLLM_API_KEY"
```

The benchmark reports actual API timing and generated-token usage, so tokens/s is computed from a live response rather than estimated.

## Vast vLLM container profile

The command below is for Vast's current vLLM template after an 80 GB-or-larger disk has been selected. It starts the OpenAI API at the template's internal `http://127.0.0.1:18000/v1`; Vast maps it to the public port advertised in `vast-capabilities`. It is intentionally a quantized `Q4_K_M` deployment to fit within an economical container disk.

```bash
MODEL_CACHE_DIR=/root/model-cache \
scripts/configure-vast-vllm.sh
```

The container's external port is protected by Vast's edge bearer token. For a stable Hostinger hostname, use a real Ubuntu VM with the Compose/Caddy topology above, or place a managed tunnel/reverse proxy in front of the container. A Hostinger A record cannot directly target Vast's changing high ports with standard HTTPS.

## GitHub Actions

`Validate` checks Terraform, Compose interpolation, and shell syntax. `Deploy GPU API` is manually dispatched and needs these repository environment secrets:

- `DEPLOY_SSH_PRIVATE_KEY`
- `VLLM_API_KEY`

For a replacement VM, launch the workflow with its new IP and the same Hostinger hostname after changing the A record. The workflow runs `terraform apply` using only ephemeral files.
