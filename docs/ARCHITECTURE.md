# Architecture and the reasoning behind it

## 1. The physical constraint: decoding is memory-bound

Generating one token requires reading every weight of a dense model once. Single-stream decode speed is therefore bounded by

```
tokens/s ≈ memory bandwidth / bytes of weights read per token
```

The NVIDIA GB10 has 119 GB of unified memory at ~273 GB/s. For a dense 27B model:

| format | weights | ceiling | observed |
|---|---:|---:|---|
| BF16 | ~54 GB | ~5 tok/s | **4.4 tok/s** (measured here) |
| FP8 | ~27 GB | ~10 tok/s | not deployed |
| NVFP4 W4A4 | ~20 GB (29 GB with vision + FP8 layers on disk) | ~13–17 tok/s | 11.5 tok/s (published) |
| NVFP4 + speculative decoding | same | ×3–5 on predictable text | **41 tok/s** (measured, code) |

Two consequences:

1. Compute is irrelevant for single-stream decode. Buying a "faster" GPU with the same bandwidth changes nothing; adding bandwidth or reading fewer bytes does.
2. Quantisation buys speed linearly with bytes. Speculative decoding buys it multiplicatively by *verifying several tokens per weight read*.

The original deployment ran BF16 and looked "broken" at 4 tok/s. It was not broken; it was at the physical ceiling of the wrong format.

## 2. Quantisation: "Q4" is not one thing

| family | bits | activations | typical loss vs BF16 | note |
|---|---|---|---|---|
| GGUF Q8_0 | 8 | 16 | ≈ 0 | llama.cpp only |
| GGUF Q6_K / Q5_K_M | 6 / 5 | 16 | ~0.5 % | |
| GGUF Q4_K_M | 4 (+6 on sensitive layers) | 16 | 1–2 % | the everyday choice |
| FP8 | 8 | 8 | ≈ 0 | Hopper/Blackwell |
| **NVFP4 (W4A4)** | 4, block of 16 + FP8 scale | **4** | ~1 % published; calibration-dependent | Blackwell FP4 tensor cores |

vLLM 0.30 on this image rejects GGUF at the loader, so llama.cpp-style Q4/Q6 was not an option without changing runtime. NVFP4 is the format Blackwell accelerates natively.

Chosen checkpoint: `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4`, an official-Qwen abliteration (refusal direction removed) quantised with ModelOpt. It retains the base model's thinking, tool calling and coding; it is *not* a coding fine-tune. Its "coding-retention" evaluation is still marked in-progress by the publisher, which is why this repo runs its own executable coding checks (see the test report).

## 3. Speculative decoding (DFlash2)

A small drafter (`z-lab/Qwen3.8-27B-DFlash2`, 3.6 GB) proposes 7 tokens per step with a block-diffusion head; the 27B target verifies all of them in one weight read and keeps the accepted prefix. Throughput scales with the acceptance length, which depends on how predictable the text is: code and structured output accept long runs; free prose accepts fewer. Published on this GPU: 11.5 → 54 tok/s. Measured here on a fine-tuned target: ~41 tok/s on code.

The drafter was trained against the base Qwen3.8-27B; abliteration is a small weight edit, so acceptance stays high. That is an assumption the acceptance metrics in the vLLM log let you check (`SpecDecoding metrics: Mean acceptance length`).

## 4. Network path and authentication

```
client ──HTTPS──► Traefik (edge VPS, Let's Encrypt) ──HTTP──► Vast mapped port ──► Caddy (token auth) ──► vLLM (--api-key)
```

- **Why a proxy at all.** A DNS A record cannot carry a port, and Vast maps container ports to random high numbers that change on recreation. The edge VPS already runs Traefik for `*.rangeltech.net`, so the route is one file provider block; the DNS record points at the VPS, not at the GPU.
- **Two token layers, one stable key.** The Vast Caddy edge requires the instance token (which changes per instance). Instead of handing that token to clients, Traefik injects it as the `C.<id>_auth_token` cookie (Caddy accepts bearer, query and cookie), leaving `Authorization: Bearer` free for vLLM's own `--api-key`. Clients only ever hold the stable `VLLM_API_KEY`.
- **Streaming.** `responseForwarding.flushInterval: 1ms` keeps SSE token streaming smooth through the proxy.
- **Replacement.** A new instance changes IP, port, label and token; `scripts/publish-endpoint.sh` rewrites exactly three managed blocks in the Traefik file (router, middleware, service) and `scripts/dns-upsert.sh` makes sure the record exists. Both are idempotent and tested for it.

## 5. Persistence contract

| what | where | survives stop/start | survives destroy/recreate |
|---|---|---|---|
| weights + drafter | `/root/models` (instance disk) | yes | no (re-downloaded by the bootstrap) |
| vLLM config | `/workspace/.env` managed block | yes | rebuilt by `configure-vast-vllm.sh` |
| route + DNS | edge VPS + Hostinger | n/a | rebuilt by `publish-endpoint.sh` / `dns-upsert.sh` |
| keys | GitHub secrets + personal-skills vault | n/a | n/a |

Nothing unusable is kept on the billed disk: the BF16 checkpoint and the GGUF tried earlier were deleted; `configure-vast-vllm.sh` deliberately downloads only the two model directories it serves.

## 6. Why the Vast container is a transition, not the end state

A Vast template is a container, not a VM: no Docker-in-Docker, no kernel modules, vLLM under Supervisor. The repository therefore has two deployment paths:

1. **Vast container profile (in production now)**: scripts above + `deploy-vast.yml`.
2. **Genuine Ubuntu GPU VM**: `compose.yaml` (vLLM + Caddy) applied by Terraform (`infra/terraform`) through SSH, triggered by `deploy.yml`.

Both are driven by the same secrets; a new machine needs only its address.

## 7. Pitfalls that cost real time (each one is verified)

1. **Wrong SSH port.** Vast lists several mapped ports; the Jupyter port (8080) is not SSH (22). SSH is `VAST_TCP_PORT_22`, read from the container environment. Hours were lost looking for it in the web UI.
2. **`supervisorctl stop vllm` does not free the port.** The API server/engine children keep `:18000` and the GPU. The next start dies with `Address already in use` while the *old* process keeps answering, which looks like success. Kill by pattern.
3. **`pkill -f "vllm serve"` kills your own shell.** The pattern also matches the command line of the shell running it. Use `pkill -f "[v]llm serve"`.
4. **`eval` in the supervisor script strips JSON quotes.** `--speculative-config {...}` must be single-quoted inside a double-quoted env value.
5. **BF16 on this GPU is bandwidth-bound**, not misconfigured (section 1).
6. **Deleting the wrong instance.** Three instances were billing at once (one errored, one stopped-with-disk, one live). "Stopped" still bills storage; only destroy stops it.
7. **Unified memory and JIT compilation.** FlashInfer compiles FP4 kernels on first run; unbounded `ninja` parallelism races the resident weights. `MAX_JOBS=4`.
8. **Background jobs die with the agent turn** when started with `nohup &`; long test runs must use the harness's background execution.
