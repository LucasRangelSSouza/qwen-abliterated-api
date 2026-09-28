# Outline

Numbers in brackets point to `02-facts-and-numbers.md`; figures to `figures/`. Word budget in parentheses.

## 1. The verdict everyone gave (250)

The 4.4 tok/s deployment, the "misconfigured" diagnosis, what it cost to keep the GPU on while people guessed. *Evidence:* BF16 measured 4.4 tok/s; three instances billing at once at one point (`03-timeline`). *No figure.*

## 2. The arithmetic that closes the case (350)

`tokens/s ≈ bandwidth ÷ bytes read per token`; 273 GB/s ÷ 54 GB ≈ 5; measured 4.4. Two consequences: compute does not matter for single-stream decode; fewer bytes buy speed linearly. *Figure 1.* Call out clearly which points are theoretical, measured, or published.

## 3. Quantisation is not one thing (350)

Weight formats (GGUF Q4/Q6/Q8, FP8, NVFP4) and what "W4A4" means; why GGUF was not available in this runtime; why FP8/NVFP4 are the Blackwell-native choices. Keep the loss numbers as "typically reported", not measured here. *Evidence:* `docs/ARCHITECTURE.md` §2.

## 4. The multiplier: verifying several tokens per read (400)

Speculative decoding with a drafter (DFlash2): 7 proposed tokens, one weight read to verify. Why it helps code and SQL (long accepted runs) and barely helps prose. *Figure 2* (decode by workload). The published 11.5 → 54 tok/s on this GPU family versus the 33–36 measured here on a different (abliterated) target.

## 5. The surprise: FP8 caught up (400)

Plain-decode arithmetic says NVFP4 should win by ~1.5×. With speculative decoding the two were indistinguishable on single-stream speed, and FP8 was faster on prefill and aggregate throughput. Present it as an overturned estimate, with the caveat that the throughput gap comes from single runs (aggregate FP8 measured twice, 98.6 and 90.6). *Evidence:* `02-facts` speed tables. *Figure 2 again or a small table.*

## 6. The silent failure (450)

fp8 KV cache: every short benchmark passed; needle tests passed up to ~21k tokens and failed in all 6 attempts from ~24.5k tokens, always with the same garbage ("duct Register Register …"): 6/12 overall against 12/12 with the bf16 KV cache on the same series, and 15/15 (2k to 30k) for the FP8-weights suite. The bf16 cache still held ~400k tokens, so the memory saving was never needed. *Figure 5* (needle retrieval by prompt length and KV dtype), *Figure 3* (prefill cost, to set expectations for long prompts). Lesson: a benchmark suite that never leaves the short-prompt regime certifies a broken deployment. Note the boundary is for this model, prompt design and vLLM version; do not generalise to fp8 KV caches in general.

## 7. Grading the grader (450)

Two real bugs and one design flaw in my own grader: helper functions from the prompt were not prepended (4 `NameError`s), a 1024-token cap cut 6 GSM8K answers, and the thinking test tied the parser contract to answer accuracy. Fixed and validated in both directions. The surprise: re-running with the fixed grader moved the totals by only -1 (HumanEval) and +3 (GSM8K), because run-to-run variation at temperature 0 is of the same order. Hence significance: with 164 problems a 2-point gap is noise; paired McNemar tests; Wilson intervals. *Figure 4* (quality vs cost with CIs). Result: Sonnet 5 is ahead on HumanEval (p = 0.03 and 0.002), the three are indistinguishable on GSM8K, and FP8 vs NVFP4 is not significant.

## 8. A yardstick and a price (300)

Same prompts and grader through Claude Code (`claude -p`, Sonnet 5 medium, tools off): 100 % / 98.5 % for US$ 2.90. Cost per 1000 tasks: ~US$ 8 vs ~US$ 0.34 with a busy GPU; break-even ≈ 4 % utilisation. State the assumptions (GPU busy, API-equivalent cost).

## 9. Making it a system, not an experiment (450)

The public URL (edge proxy, cookie injection so clients hold one key), idempotent scripts, Terraform whose plan after apply is empty, a rebuild workflow, and the tests that prove idempotency (including a real provider stop/start). One or two concrete bugs those tests found (configure restarted a server that was still loading; the provider silently dropped the SSH key). The Whisper sidecar as an example of completing a modality properly (two vLLM processes on one GPU, start order). *Snippets 1–3.*

## 10. What I would not claim (250)

Refusal behaviour not measured; a dense 27B is not a frontier model for agentic work; prose speed; long-prompt prefill; shared-GPU contention not measured; cost assumes a busy GPU. End with the repository link and the one-command reproduction.

## Figure-to-section map

| figure | section | file |
|---|---|---|
| 1 | 2 | `figures/fig1_bandwidth_ceiling.png` |
| 2 | 4–5 | `figures/fig2_decode_by_workload.png` |
| 3 | 6 | `figures/fig3_prefill_vs_context.png` |
| 4 | 7–8 | `figures/fig4_quality_vs_cost.png` |
| 5 | 6 | `figures/fig5_kv_cache_needle.png` (generated once the KV experiment file exists) |

## Optional sidebars (each ≈ 100 words)

Vast port mapping (SSH is the port mapped to 22, not the Jupyter one) · why `supervisorctl stop` does not free a port · why `pkill -f "vllm serve"` kills your own shell · a stopped GPU instance still bills its disk · a queue that keeps going after a failed deploy benchmarks the previous model under the new label.
