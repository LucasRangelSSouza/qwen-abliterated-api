# Glossary and reader questions

## Glossary (one sentence each; expand in the article only where the reader needs it)

- **Decode / prefill** — prefill processes the prompt in parallel (compute-bound); decode generates one token at a time (memory-bound). Time to first token is mostly prefill; tokens per second is decode.
- **Memory bandwidth** — how many gigabytes per second the GPU can read from its memory; ~273 GB/s on the GB10 used here.
- **Quantisation** — storing weights in fewer bits. *Weight-only* (W4A16: 4-bit weights, 16-bit activations) vs *weight-and-activation* (W4A4, W8A8).
- **FP8 / NVFP4 / GGUF Q4_K_M** — an 8-bit float format; NVIDIA's 4-bit float format with block scales for Blackwell tensor cores; the everyday 4-bit format of the llama.cpp ecosystem. Not interchangeable across runtimes.
- **KV cache** — the attention keys and values kept for every token already processed; it has its own dtype, separate from the weights.
- **Speculative decoding** — a small *drafter* proposes several tokens; the large model verifies them in one pass and keeps the accepted prefix. Output distribution is unchanged in exact implementations; speed depends on the acceptance length.
- **Acceptance length** — the average number of drafted tokens accepted per step; high for code, low for free prose.
- **Abliteration** — removing the "refusal direction" from a model's weights so it stops refusing. Whether and how far it does is a property of each checkpoint; not measured here.
- **Needle in a haystack** — hide a fact in a long filler prompt and ask for it; a cheap test of long-context integrity.
- **pass@1** — the fraction of problems solved by a single greedy sample.
- **Wilson interval / McNemar test** — a confidence interval for a proportion; a paired test on per-problem agreement between two models on the same problems.
- **Idempotent** — running it again changes nothing when the desired state already holds.
- **Edge proxy** — the always-on host that terminates TLS and forwards to a GPU whose address changes.
- **Sidecar** — a second process deployed and routed together with the main one (here, Whisper next to the LLM).

## Questions the reader will ask

**Why not just use an API?** For this workload a frontier API is better in quality (4 points on HumanEval here) and, at low utilisation, cheaper. Self-hosting wins when the GPU is busy (~4 % utilisation break-even in this test), when the data cannot leave, or when the model must be one that hosted APIs do not offer. The article should say all three.

**Is a 27B open model as good as Sonnet 5?** No, not on this test and not for long agentic work. On two tasks with a plain completion, Sonnet 5 was 4 points ahead on HumanEval (significant) and level on GSM8K (not distinguishable). Do not generalise.

**Why is prose so slow?** Speculative decoding only helps when the drafter guesses well; free prose is unpredictable, so acceptance is low and decode falls back toward the plain-decoding rate (~14 tok/s here).

**Why does the first token take 100 seconds on a long prompt?** Prefill cost grows with the prompt: ~12 s at 30k tokens, ~100 s at 140k on this GPU. A repeated prefix is served from the prefix cache.

**Why FP8 rather than the 4-bit format everyone recommends?** Quality was statistically the same, FP8 was faster on prefill and on aggregate throughput once speculative decoding was on, and single-stream speed was equal. 4-bit would still win for plain decoding, or where memory (not speed) is the constraint.

**What happens if the GPU provider gives the machine to someone else?** The instance may not restart on the same GPU. That is why everything is rebuildable from the repository, with weights re-downloaded in about ten minutes.

**Is the abliterated model dangerous to expose?** That is an operational question the article should raise honestly: the endpoint is behind a key, the repository does not measure or claim refusal behaviour, and anyone publishing a similar service should decide their own acceptable-use policy first.

**Can I run this on a consumer GPU?** The arithmetic transfers (bandwidth ÷ bytes); the specific numbers, the FP4 format and the drafter recipe do not. A 24 GB card needs a 4-bit GGUF and a different runtime.

**How much did it cost?** The GPU ran at about US$ 0.45/h. The complete set of experiments occupied the instance for many hours; the yardstick cost US$ 2.90. Give the reader the per-1000-task figures instead of the total, which depends on how long you leave it on.
