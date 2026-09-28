# Article notes (Medium / portfolio)

Working title options
- *I rented a GB10 for $0.45/h and turned a 27B abliterated Qwen into a 40 tok/s OpenAI-compatible API. Here is every mistake.*
- *Decoding is memory-bound: how speculative decoding got a 27B model from 4 to 40 tokens/s on the same GPU.*

Audience: engineers who self-host LLMs or are deciding between paying per token and renting a GPU. Assume they know what vLLM and quantisation are; do not assume they know the memory-bandwidth arithmetic.

## Spine of the story

1. **The complaint.** A 27B model at 4 tok/s on a "very good" GPU. Everyone says the machine must be misconfigured.
2. **The arithmetic.** It was not. `tokens/s ≈ bandwidth / bytes read per token` → 273 GB/s ÷ 54 GB = ~5. The measured 4.4 tok/s is the ceiling of the wrong format. (docs/ARCHITECTURE.md §1)
3. **Three levers, only one multiplies.** Smaller weights buy speed linearly; a bigger GPU with the same bandwidth buys nothing; speculative decoding verifies several tokens per weight read (4 → 35–41 tok/s on code).
4. **The surprise.** After speculative decoding, FP8 was as fast as NVFP4 on this box (34.8 vs 34.9 tok/s on code; 99 vs 78 tok/s aggregate at 4 clients). Weight bytes stop being the bottleneck once the drafter shifts the workload toward compute. This overturned my own estimate and is the most publishable finding.
5. **The silent killer.** fp8 KV cache corrupted generations past ~20k tokens (garbage such as "Register Register …", 12/36 needle failures) while short benchmarks all passed. bf16 KV: 0 failures up to 140k tokens. Lesson: a benchmark suite that only uses short prompts certifies a broken deployment.
6. **Measuring quality honestly.** Three of my own harness bugs made the model look worse than it is (helper functions from the prompt not prepended; a 1024-token cap that truncated chain-of-thought; a wrong expected value I had written myself). After fixing them the numbers moved by ~3 points. Lesson: grade the grader before you grade the model.
7. **A yardstick.** Same prompts, same local grader, run through Claude Code (`claude -p`, Sonnet 5 medium, tools off): 164/164 HumanEval and 197/200 GSM8K for US$ 2.90. Cost per task vs the rented GPU.
8. **Ops as code.** Idempotent scripts (config, edge route, DNS, power), a Terraform module whose `plan` after `apply` is empty, a one-click workflow that rebuilds on any container by changing only its address, and the tests that prove idempotency: re-runs change nothing, deliberate config drift is repaired, forced kills recover without downloads, and a real stop/start through the Vast API comes back on the same address. Found by those tests: a `configure` that restarted a server still loading its weights.
9. **Completing the modalities.** Vision worked out of the box; audio did not (the model has no audio input and says so with a clean 400). The fix was architectural, not a workaround: a Whisper sidecar on the same GPU, started after the LLM, routed by path on the same domain and protected by the same key, provisioned by the same script/Terraform/workflow. Two vLLM processes on one unified-memory GPU is a nice small lesson in start-up ordering.
10. **Honest limits.** A dense 27B at 4 bits is not Sonnet-class for agentic work; prose is 13 tok/s because the drafter accepts fewer tokens on free text; long prompts cost minutes of prefill; a GPU shared with a real-time workload will lose throughput.

## Facts and numbers to cite (all in this repo)

- Hardware: NVIDIA GB10, 119 GB unified memory, ~273 GB/s; Vast.ai, US$ 0.449/h running, ~US$ 0.007/h stopped.
- Stack: vLLM 0.30, `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4` and the same lineage's BF16 with online FP8, `z-lab/Qwen3.8-27B-DFlash2` drafter (7 speculative tokens).
- Speech-to-text: `openai/whisper-large-v3-turbo` served by vLLM as a sidecar (0.10 of GPU memory), `/v1/audio/transcriptions`.
- Edge: Traefik + Let's Encrypt on a small VPS, Hostinger DNS API, cookie injection so clients hold one stable key.
- Speed, context, quality, cost: docs/RESULTS.md (generated from reports/*.json, reproducible with `tests/`).

## Pitfalls worth a sidebar each

- Vast lists several mapped ports; SSH is the one mapped to 22, not the Jupyter one.
- `supervisorctl stop` does not free the port; `pkill -f "vllm serve"` kills your own shell (use `[v]llm`).
- Vast rewrites `authorized_keys` from account keys: register the key on the account and the instance through the API.
- "Stopped" instances still bill storage; only destroy stops billing, and destroy deletes the weights.
- Two benchmark runs sharing one GPU by accident (a `nohup` process outlived its shell) silently contaminate every number.
- A queue that keeps going after its deploy step failed benchmarks the previous model under the new label. Make deploy verify what it deployed.

## Figures to draw

1. Bytes-per-token vs bandwidth ceiling per format (bar chart with measured points).
2. Decode tok/s by workload (code, SQL, prose) × thinking on/off, both precisions.
3. Prefill time vs prompt length (30k → 140k), both precisions.
4. Quality vs cost per 1000 tasks: Sonnet 5 medium vs Qwen FP8 vs Qwen NVFP4.
5. Request path diagram: client → Traefik → Vast Caddy → vLLM, with where each token/cookie is added.

## Claims that need care

- "Same abliterated weights" is true by lineage (the NVFP4 card names the BF16 as its base; FP8 is online quantisation of that BF16); refusal behaviour was neither benchmarked nor spot-checked: say so, and cite the publisher's card only.
- The Sonnet 5 numbers come from a plain completion without tools; they are a yardstick for these two tasks, not a general ranking.
- Cost per task for the GPU assumes it is busy; an idle rented GPU costs the same per hour.
