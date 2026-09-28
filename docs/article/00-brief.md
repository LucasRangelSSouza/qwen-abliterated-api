# Brief

## One-sentence thesis

Decoding a dense LLM is limited by memory bandwidth, not compute; once you understand that, the fix for a "slow GPU" is to read fewer bytes per token and to verify several tokens per read, and the rest of the work is making the result measurable, reproducible and honest about its limits.

## Angle

Not "how to run Qwen". The article is a worked example of engineering judgement under uncertainty: a wrong diagnosis ("the machine is misconfigured"), an arithmetic that corrected it, a measurement that overturned my own estimate (FP8 as fast as NVFP4), a silent failure that no short benchmark could see (fp8 KV cache), and a grader that had to be audited before it could grade anything. The tone is first-person, specific, and unshowy: what I believed, what I measured, what I changed.

## Audience

Engineers who self-host or are deciding whether to. They know what vLLM and quantisation are. They do not know the bandwidth arithmetic, the difference between weight quantisation and KV-cache quantisation, or why speculative decoding changes which format wins. Secondary: hiring managers reading it as evidence of platform-engineering judgement (see the `profissional` skill).

## Title options

1. *I rented a GPU for $0.45/h and my 27B model ran at 4 tokens per second. It wasn't broken.*
2. *Decoding is memory-bound: how speculative decoding took a 27B model from 4 to 35 tokens per second on the same GPU*
3. *Every mistake I made putting an open LLM behind an OpenAI-compatible API (and the tests that caught them)*

Recommended: 1 for the headline, 2 as the subtitle.

## Opening hook (draft, to be rewritten in the author's voice)

The model answered at 4.4 tokens per second on a GPU I was paying US$ 0.45 an hour for, and everyone I asked said the same thing: something is misconfigured. Nothing was. A 27-billion-parameter model in 16-bit precision is 54 GB, the GPU can read about 273 GB per second, and 273 divided by 54 is five. The machine was running at the physical ceiling of the wrong number format.

## Length and shape

2 800 – 3 500 words, 5 figures, 3–4 short code excerpts. Sections as in `01-outline.md`. A Portuguese version (for LinkedIn and the Brazilian audience) can be a shorter re-telling of sections 1–4 plus the results table.

## What a reader should be able to do afterwards

1. Compute the decode ceiling of any model/GPU pair before renting anything.
2. Decide between FP8, NVFP4 and GGUF-style quantisation for a given runtime, and know what speculative decoding does to that decision.
3. Write a long-context test that would have caught the KV-cache bug.
4. Structure an evaluation so that the grader, the sample size and the significance are not the weak point.
5. Reproduce the whole deployment from the repository.

## Derivatives (same evidence, different length)

- **LinkedIn post (≈ 150 words):** the 273 ÷ 54 = 5 hook, one number (4 → 35 tok/s), the silent KV-cache failure, link.
- **CV bullet:** "Designed and operated a self-hosted OpenAI-compatible LLM service (vLLM, speculative decoding, FP8) on rented Blackwell hardware: 8× decode speed-up, Terraform-managed and idempotent, benchmarked against a frontier model with paired significance tests; ~23× lower cost per task at high utilisation."  (Check the numbers against `02-facts-and-numbers.md` before use; the "8×" is 4.4 → 33–36 tok/s.)
- **Interview narrative (STAR):** situation = 4 tok/s and a "misconfigured" verdict; task = reach interactive speed and prove it; action = arithmetic, quantisation, speculative decoding, tests, IaC; result = measured numbers, statistically compared with a frontier model, rebuildable from the repo; what I would do differently = test long context from day one and audit the grader before the model.

## Not in scope of the article

- Refusal behaviour of the abliterated model: it was not measured and no claim should be made beyond the publisher's model card.
- Any credential, IP address, instance identifier or domain that is not already public (see the checklist in `09-writing-guide.md`).
