# Sources

Status legend: **opened** = the page or file was fetched and read during this work; **search summary** = only seen as a search-result snippet, open it before citing; **memory** = a standard reference recalled without opening, verify with the `academico` skill before citing.

## Measured on this hardware or model family by others

| source | status | supports | how it was used |
|---|---|---|---|
| Kubesimplify, *Running Qwen3.8-27B on DGX Spark* — https://blog.kubesimplify.com/qwen3-8-27b-on-dgx-spark | opened | NVFP4 with vLLM: 11.5 tok/s single stream, 84.3 tok/s aggregate at 10 concurrent requests; FlashInfer FP4 path | the "plain NVFP4 decoding" point in figure 1 is this published figure, labelled as published |
| madeye, *DFlash2 on GB10 — 4.70× decode speedup for Qwen3.8-27B* — https://madeye.github.io/qwen3.8-dflash2-gb10/ | opened | 54.3 tok/s with a block-diffusion drafter on NVFP4, acceptance length 6.38, vLLM 0.28, `MAX_JOBS=4` to avoid OOM during kernel compilation | the recipe (target + drafter + 7 speculative tokens) |
| github.com/madeye/qwen3.8-dflash2-gb10 (`serve.sh`) — https://github.com/madeye/qwen3.8-dflash2-gb10 | opened (raw script) | exact `--speculative-config` JSON for `method: dflash`; the recipe also sets an fp8 KV cache | note for the article: that recipe uses fp8 KV and targets the *base* model; the corruption reported here was observed on a different (abliterated) target, on long prompts, in a different vLLM version; it is not a claim about that repository |
| NVIDIA Developer Forums, *Qwen3.8-27B on DGX Spark using vLLM: NVFP4 vs FP8 performance* — https://forums.developer.nvidia.com/t/qwen3-8-27b-on-dgx-spark-using-vllm-nvfp4-vs-fp8-performance/380258 | search summary | NVFP4 ahead of FP8 in plain decoding on this GPU | context for the "estimate before measuring" in the FP8 section |
| NVIDIA Developer Forums, *Qwen3.8-27B-NVFP4 on a single DGX Spark — up to 1M context, vLLM+MTP measurements* — https://forums.developer.nvidia.com/t/qwen3-8-27b-nvfp4-on-a-single-dgx-spark-up-to-1m-context-vllm-mtp-measurements/380244 | search summary | MTP speculative decoding can hard-reboot this hardware at specific settings | reason to use the DFlash2 drafter rather than MTP here |
| github.com/Fayyiz55/qwen38-dgx-spark, github.com/nabe2030/dense-27b-31b-dgx-spark | search summary | other benchmarks of dense 27B models on this GPU | background only |

## Models

| source | status | supports |
|---|---|---|
| `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4` model card — https://huggingface.co/Blackfrost-AI/Qwen3.8-27B-ABLITERATED-NVFP4 | opened (README head) | quantised W4A4 NVFP4 derivative of the publisher's BF16; "not a coding fine-tune"; coding-retention evaluation still in progress; base model Qwen/Qwen3.8-27B |
| `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16` — https://huggingface.co/Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16 | opened (API metadata) | base model `Qwen/Qwen3.8-27B`; the BF16 master that the NVFP4 card names as its base |
| `OBLITERATUS/Qwen3.8-27B-OBLITERATED` — https://huggingface.co/OBLITERATUS/Qwen3.8-27B-OBLITERATED | opened (API metadata) | the checkpoint the project started with; same base model |
| `z-lab/Qwen3.8-27B-DFlash2` (drafter) | via the madeye page | drafter size and role; not opened directly |
| `openai/whisper-large-v3-turbo` | used, model card not read | the speech-to-text model served by the sidecar |

## Provider and tooling

| source | status | supports |
|---|---|---|
| `/etc/vast-agents-guide.md` on the instance | opened | container port mapping in `VAST_TCP_PORT_*`, storage persistence rules, the container-scoped `CONTAINER_API_KEY`, the Caddy auth edge accepting bearer, query and cookie |
| vLLM behaviour (health endpoint, `--api-key`, Whisper serving, GGUF rejection, audio limits) | observed directly, docs not consulted | cite the observation ("in vLLM 0.30.0 …"), not documentation |
| Traefik file provider, Let's Encrypt HTTP challenge | observed directly | as above |

## Datasets and methods

| source | status | note |
|---|---|---|
| HumanEval — https://github.com/openai/human-eval (data file `HumanEval.jsonl.gz`) | opened | 164 problems, pass@1; Chen et al., *Evaluating Large Language Models Trained on Code*, 2021 (arXiv 2107.03374) — **memory** |
| GSM8K — https://github.com/openai/grade-school-math | opened (test set) | first 200 test problems used; Cobbe et al., *Training Verifiers to Solve Math Word Problems*, 2021 (arXiv 2110.14168) — **memory** |
| Speculative decoding — Leviathan, Kalman, Matias, *Fast Inference from Transformers via Speculative Decoding*, ICML 2023 (arXiv 2211.17192) | **memory** | the general technique behind the drafter |
| vLLM — Kwon et al., *Efficient Memory Management for Large Language Model Serving with PagedAttention*, SOSP 2023 (arXiv 2309.06180) | **memory** | the serving engine |
| Wilson score interval (Wilson 1927, JASA) and exact McNemar test (McNemar 1947, Psychometrika) | **memory** | the two statistical tools used in `tests/make_results.py` |

## Rules for citing

1. Anything marked **search summary** or **memory** must be opened and checked before it appears in the article.
2. Published numbers (11.5, 54.3 tok/s) are always labelled as published and never mixed with measured ones in a chart without a visual difference (figure 1 does this).
3. Model names, versions and dates belong to 2026; re-check that links still resolve before publishing.
