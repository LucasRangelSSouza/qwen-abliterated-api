# Claims ledger

Every claim the article may make, the file that supports it, its type, and what must not be said. Types: **measured** (a file in `reports/` contains it), **published** (someone else's number), **derived** (arithmetic on measured/published inputs), **hypothesis** (plausible explanation, not tested), **assumption** (stated condition). Re-read this file after any re-run.

| # | claim | type | evidence | do not say |
|---|---|---|---|---|
| 1 | The BF16 deployment ran at ~4.4 tok/s | measured | early suite runs (transcript) and `reports/runs/run-1.log` era; ceiling arithmetic in `docs/ARCHITECTURE.md` §1 | that the exact BF16 run is a saved JSON file (it is not; only the later runs are) |
| 2 | ceiling ≈ 273 GB/s ÷ 54 GB ≈ 5 tok/s | derived | 273 GB/s is the published memory bandwidth of this GPU (provider/NVIDIA figure, not measured here) | that bandwidth was measured |
| 3 | plain NVFP4 decoding ≈ 11.5 tok/s | published | `05-sources.md` (Kubesimplify) | that it was measured on this instance |
| 4 | FP8 + drafter: 33–36 tok/s on code/SQL, thinking off | measured | `02-facts` speed tables (three runs: 34.8, 33.4 for code) | "40 tok/s": the 41 tok/s of the first NVFP4 test was a short-prompt best case |
| 5 | free prose ≈ 14 tok/s | measured | `02-facts` | that the drafter "always" helps |
| 6 | FP8 as fast as NVFP4 in single-stream decode with speculative decoding | measured | `02-facts` (34.8/33.4 vs 34.9 on code) | that FP8 is *faster* in throughput as a proven fact: FP8 aggregate was measured twice (98.6, 90.6) vs NVFP4 once (78.5) |
| 7 | the reason FP8 caught up is that the drafter moves work toward compute | hypothesis | none (not tested) | present it as the explanation without "probably" |
| 8 | with the fp8 KV cache the needle test passed up to ~21k tokens and failed 6/6 from ~24.5k tokens with identical degenerate output (6/12 overall); with bf16 KV: 12/12 on the same series, 15/15 (2k-30k) in the FP8-weights suite | measured | `reports/longctx/longctx-fp8kv-experiment.json`, `longctx-kv-auto.json`, `reports/runs/run-final-fp8.json` (context section); first observation in `reports/runs/run-1.json` (28k) | that fp8 KV is broken in general (one model, one vLLM version, one prompt family); that the boundary is exactly 21–24.5k (only four sizes were tested); the control for the same-series comparison used NVFP4 weights, the FP8-weights control used slightly different sizes |
| 9 | needle retrieval 8/8 up to ~140k tokens (FP8 and NVFP4, bf16 KV) | measured | `reports/longctx/longctx-*-160k.json` | that quality on real long documents is proven: the haystack is synthetic filler |
| 10 | prefill ≈ 12 s at 30k tokens and ≈ 100 s at 140k (FP8) | measured | `02-facts` long-context table | that it scales linearly, or that it applies with a warm prefix cache |
| 11 | HumanEval / GSM8K: Sonnet 5 medium 164/164, 197/200; Qwen FP8 158/164, 192/200; NVFP4 154/164, 194/200 | measured | `reports/quality/*.json` | that these are model rankings: two tasks, greedy, one sample each |
| 12 | Sonnet 5 is better on HumanEval, the three are indistinguishable on GSM8K, FP8 vs NVFP4 not significant | measured | McNemar in `docs/RESULTS.md` | "FP8 is better than NVFP4" |
| 13 | run-to-run variation at temperature 0 is a few problems | measured | first vs second NVFP4 run (155 → 154, 191 → 194) | that the variation was characterised: it is two runs |
| 14 | the first grader had two bugs and a design flaw | measured | `reports/quality/quality-nvfp4-harness-v1.json` failure text; fix commits | that the bugs "understated the model by X points" (the re-run did not show a clean X) |
| 15 | cost ≈ US$ 0.34 vs ≈ US$ 8 per 1000 tasks; break-even ≈ 4 % utilisation | derived | `docs/RESULTS.md` (cost) | that the GPU is cheaper in general: it assumes a busy GPU; Claude's figure is API-equivalent |
| 16 | 16/16 idempotency checks including a real provider stop/start; API back in 472 s, Whisper 29 s later | measured | `reports/ops/idempotency.json` | that the provider guarantees the same GPU on restart |
| 17 | Terraform `plan` after `apply` shows no changes | measured | recorded in the session; reproducible with `terraform plan -detailed-exitcode` | that a saved artefact of it exists in `reports/` (it does not) |
| 18 | vision works; audio input is rejected with 400; Whisper sidecar transcribes and the round trip works | measured | `reports/runs/run-final-fp8.json`, `reports/ops/pipeline-audio.json` | that the model "understands audio" |
| 19 | the model is abliterated | published | model cards (`05-sources.md`) | refusal behaviour: only the 37-prompt probe in `docs/REFUSAL.md` (Qwen 0 refused, Sonnet 5 3 refused), not a general claim |
| 20 | the two precisions are the same abliterated weights | derived | the NVFP4 card names the BF16 as its base; FP8 is online quantisation of that BF16 | that the weights were compared bit-for-bit |
| 21 | 5 s of Portuguese speech transcribed in ~2 s, exact punctuation | measured | `reports/ops/pipeline-audio.json`, `run-final-fp8.json` (one sentence, synthetic voice) | that Whisper accuracy was evaluated |
| 22 | US$ 0.449/h running, ~US$ 0.007/h stopped | measured | provider API responses during the session (prices change) | that these prices hold |

## Claims deliberately excluded

- Any refusal claim beyond the 37-prompt probe in `docs/REFUSAL.md`, and anything about GPT models (not measured).
- "Same as Sonnet 5" or any general model ranking.
- Any statement about latency under a second real-time workload sharing the GPU (not measured).
- The figure "12 of 36 needle failures with fp8 KV", which appeared in an earlier draft and was **removed because no saved file supported it** (see `03-timeline-and-mistakes.md` §13).

## Pre-publication audit

1. For every number in the draft, find its row above and open the evidence file.
2. Search the draft for the words "always", "proves", "faster than" and "better than": each needs a row with a significance or a caveat.
3. Confirm that no IP address, domain, instance id, port, token or key from `reports/` or the docs appears (see `09-writing-guide.md`).
