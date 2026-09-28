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
                        ├─ target : Qwen3.8-27B abliterated, BF16 checkpoint quantised to FP8 at load   52 GB on disk
                        └─ drafter: DFlash2 block-diffusion speculative decoder   3.6 GB
   Whisper sidecar :3000 (vLLM, openai/whisper-large-v3-turbo, 0.10 of GPU memory)
      ▲ Traefik routes /v1/audio/* straight to it; same bearer key
```

## Measured result (default profile: FP8)

| metric | value |
|---|---|
| time to first token, short prompt | 1.3 - 3 s |
| decode, code / SQL, thinking off | ~35 - 38 tok/s |
| decode, thinking on | 20 - 28 tok/s (free prose ~14-19) |
| aggregate throughput, 4 / 8 clients | ~99 / ~107 tok/s |
| context | 160 000 tokens configured; needle retrieval 8/8 up to 140k (prefill 12 s at 30k, ~100 s at 140k; repeat prompt ~2 s via prefix cache) |
| HumanEval / GSM8K | 96.3 % / 96.0 % (Sonnet 5 medium on the same grader: 100 % / 98.5 %) |
| vision | image_url (base64 or URL) works |
| audio | speech-to-text sidecar (`whisper-large-v3-turbo` on the same GPU): `POST /v1/audio/transcriptions`, same key; then chat. The chat model itself has no audio input (rejected with 400) |
| restart (kill -> healthy), weights on local disk | ~7 min, no download (real Vast stop/start: ~8 min) |

`PROFILE=nvfp4` selects the pre-quantised NVFP4 checkpoint: same quality within noise, slower prefill and lower aggregate throughput. Full tables, statistics and cost per task: [docs/RESULTS.md](docs/RESULTS.md). Why the numbers are what they are: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
- Images: `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}` in the message content.
- Audio: `curl $OPENAI_BASE_URL/audio/transcriptions -H "Authorization: Bearer $OPENAI_API_KEY" -F model=whisper -F language=pt -F file=@question.wav`, then send the text to chat.
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
| `tests/suite.py` | end-to-end suite (auth, speed, thinking on/off, heavy context, coding, tools, vision, audio contract, parallel Q&A, concurrency, stability) |
| `tests/quality.py`, `quality_claude.py` | HumanEval + GSM8K on the endpoint and on Claude Code (the yardstick) with one shared grader |
| `tests/pipeline_audio.py` | speech -> Whisper sidecar -> Qwen round trip, entirely through the public API |
| `tests/run_all.py` | one entry point: suite, audio pipeline, idempotency, reports |
| `tests/idempotency.py`, `restart_cycles.py` | idempotency, config-drift repair, forced restarts, real Vast stop/start |
| `tests/make_report.py` | JSON → Markdown report |
| `.github/workflows/deploy-vast.yml` | one-click rebuild on any Vast container with sshd (profile input, default fp8) |
| `infra/terraform-vast/` | Terraform (no provider needed) for the Vast profile: configure, Traefik route, DNS, acceptance test; `plan` after `apply` is empty |
| `.github/workflows/deploy.yml` + `infra/terraform` | the same for a genuine Ubuntu GPU VM (Docker + Caddy + Terraform) |
| `docs/` | architecture, runbook, article notes |
| `reports/` | raw JSON and rendered reports of every run, organised by kind (see `reports/README.md`) |

## Rebuild on another machine

1. Rent a GPU (Blackwell for NVFP4) whose template ships `sshd`; add the public half of `DEPLOY_SSH_PRIVATE_KEY` to the account.
2. Run **Actions → Deploy to Vast instance** with `ssh_host`, `ssh_port`, `api_port`, `instance_id`.
3. The workflow downloads weights (skipped if present), writes the vLLM config, publishes the route, upserts DNS, waits for `/v1/models` and smoke-tests a completion.

Repository secrets: `DEPLOY_SSH_PRIVATE_KEY`, `VLLM_API_KEY`, `HOSTINGER_API_KEY`, `EDGE_SSH_PRIVATE_KEY`, `VAST_API_KEY`, `VAST_INSTANCE_ID`. The same values live in the private personal-skills vault (`secrets/qwen-api.env`).

Turning the machine on and off: [docs/RUNBOOK.md](docs/RUNBOOK.md).
