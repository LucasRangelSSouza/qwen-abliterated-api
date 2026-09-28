# Reports

Raw data and rendered reports of every test run. Nothing here is hand-typed: `docs/RESULTS.md` is generated from these files by `tests/make_results.py`.

| folder | contents | produced by |
|---|---|---|
| `runs/` | endpoint suite runs (`run-*.json`, logs) and their rendered reports (`REPORT-*.md`) | `tests/suite.py`, `tests/make_report.py`, `tests/run_all.py` |
| `quality/` | HumanEval + GSM8K per model (`quality-*.json`), greedy-output fidelity (`fidelity-*.json`), the Sonnet 5 yardstick | `tests/quality.py`, `tests/quality_claude.py`, `tests/fidelity_diff.py` |
| `longctx/` | needle-in-a-haystack from 30k to 140k tokens per variant, and the KV-cache dtype experiment | `tests/longctx.py`, `tests/queue_variants.py` |
| `ops/` | idempotency and restart results, audio pipeline result, the experiment queue log | `tests/idempotency.py`, `tests/restart_cycles.py`, `tests/pipeline_audio.py` |

Run names

- `run-1`: first suite run (NVFP4, fp8 KV cache, 32k context): it exposed the long-context corruption.
- `run-2`: NVFP4, bf16 KV cache, 32k context.
- `run-fp8-160k`: FP8 profile, 160k context.
- `run-new-sections`, `run-audio-check`: partial runs of the vision / audio / parallel / transcription sections while they were added.
- `run-final-*`: full run of the final default profile (see `docs/RESULTS.md`).
- `quality-nvfp4-harness-v1.json`: NVFP4 quality with the first grader, kept on purpose: the grader had bugs (missing helper functions, 1024-token cap). Do not compare it with the other files.
