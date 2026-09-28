# Qwen Abliterated API

An OpenAI-compatible endpoint for an abliterated Qwen3.8-27B (thinking + coding + tool calling) running on a rented NVIDIA GB10, published at a stable HTTPS URL on your own domain, and rebuildable on any other machine by changing only its address.

```text
client (OpenAI SDK / curl)
   │  https://qwen.rangeltech.net/v1     Authorization: Bearer <VLLM_API_KEY>
   ▼
Traefik on the edge VPS  (TLS: Let's Encrypt, DNS: Hostinger A record)
   │  injects the Vast edge cookie, streams responses (flushInterval 1ms)
   ▼
Vast.ai container (GB10, 119 GB unified memory)
   Caddy edge :8000 ─► vLLM 0.30 :18000
                        ├─ target : Qwen3.8-27B abliterated, NVFP4 (W4A4)        29 GB
                        └─ drafter: DFlash2 block-diffusion speculative decoder   3.6 GB
```

## Measured result

| metric | value |
|---|---|
| time to first token | 0.7 – 2.2 s |
| decode, code, thinking off | ~41 tok/s |
| decode, thinking on | ~37 tok/s |
| context | 32 768 tokens |
| restart (kill → healthy), no download | ~4.5 min |

Full numbers, methodology and per-test tables: [reports/REPORT.md](reports/REPORT.md). Why these numbers are what they are: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Use it

```bash
export OPENAI_BASE_URL=https://qwen.rangeltech.net/v1
export OPENAI_API_KEY=...            # secrets/qwen-api.env in personal-skills, or the VLLM_API_KEY repo secret
curl "$OPENAI_BASE_URL/chat/completions" -H "Authorization: Bearer $OPENAI_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen-abliterated","messages":[{"role":"user","content":"Write a retry decorator."}],
       "chat_template_kwargs":{"enable_thinking":false}}'
```

```python
from openai import OpenAI
c = OpenAI(base_url="https://qwen.rangeltech.net/v1", api_key="...")
r = c.chat.completions.create(model="qwen-abliterated", temperature=0, max_tokens=2048,
      messages=[{"role": "user", "content": "Write a robust Python retry helper."}],
      extra_body={"chat_template_kwargs": {"enable_thinking": True}})   # reasoning arrives in message.reasoning
```

- `enable_thinking: true` returns the chain of thought separately in `reasoning`, the answer in `content`.
- Tool calling works with `tool_choice: "auto"` (`qwen3_xml` parser).
- The model card recommends `temperature=0`, and thinking off for the abliterated behaviour to hold.

## Repository map

| path | what |
|---|---|
| `SPEC_OPERACIONAL.md` | the operating contract (access, persistence, idempotency, API, acceptance) |
| `scripts/configure-vast-vllm.sh` | idempotent instance configuration: weights, drafter, vLLM args, restart only on drift |
| `scripts/publish-endpoint.sh` | idempotent Traefik route + auth-cookie injection on the edge VPS |
| `scripts/dns-upsert.sh` | idempotent Hostinger A record (touches only the named record) |
| `scripts/vast-power.sh` | start / stop / status of the Vast instance through its API |
| `scripts/smoke-test.sh`, `benchmark.sh` | quick checks |
| `tests/suite.py` | end-to-end test + benchmark suite (auth, speed, thinking on/off, heavy context, coding with executed asserts, tools, concurrency, stability) |
| `tests/idempotency.py` | idempotency and restart tests |
| `tests/make_report.py` | JSON → Markdown report |
| `.github/workflows/deploy-vast.yml` | one-click rebuild on any Vast container with sshd |
| `.github/workflows/deploy.yml` + `infra/terraform` | the same for a genuine Ubuntu GPU VM (Docker + Caddy + Terraform) |
| `docs/` | architecture, runbook, article notes |
| `reports/` | raw JSON and rendered reports of every test run |

## Rebuild on another machine

1. Rent a GPU (Blackwell for NVFP4) whose template ships `sshd`; add the public half of `DEPLOY_SSH_PRIVATE_KEY` to the account.
2. Run **Actions → Deploy to Vast instance** with `ssh_host`, `ssh_port`, `api_port`, `instance_id`.
3. The workflow downloads weights (skipped if present), writes the vLLM config, publishes the route, upserts DNS, waits for `/v1/models` and smoke-tests a completion.

Repository secrets: `DEPLOY_SSH_PRIVATE_KEY`, `VLLM_API_KEY`, `HOSTINGER_API_KEY`, `EDGE_SSH_PRIVATE_KEY`. The same values live in the private personal-skills vault (`secrets/qwen-api.env`).

Turning the machine on and off: [docs/RUNBOOK.md](docs/RUNBOOK.md).
