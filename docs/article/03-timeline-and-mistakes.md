# Timeline, wrong turns and lessons

All on 2026-09-28 unless noted. Times are approximate and only used for ordering. Each entry says what was believed, what happened, and the lesson; the evidence file is named where one exists. The mistakes are the most reusable part of the story, so they are kept in.

## Starting point (inherited)

A previous working session had rented a Vast.ai GB10 instance and served the BF16 checkpoint of an abliterated Qwen3.8-27B. State on arrival: the API answered at ~4 tokens/s, SSH into the instance was "not working" (all access went through the provider's web terminal), and at one point three instances had been billing at the same time (one errored, one stopped with its disk, one live). Two were destroyed after explicit confirmation.

**Lesson:** a stopped GPU instance still bills its disk; only *destroy* stops billing, and destroy deletes the weights. Check what is running before doing anything else.

## 1. Getting in (morning)

- **Believed:** SSH was on the port the earlier session had tried (the one that showed up next to the web terminal).
- **Reality:** that port was Jupyter. SSH is the port mapped to container port 22 (`VAST_TCP_PORT_22`), visible only from inside the container's environment. A Jupyter token found in the previous session's logs was enough to open a terminal and read it.
- **Lesson:** provider dashboards list many ports; the mapping for each container port is in the container's environment. Read it there.

## 2. The 4-tokens-per-second "bug"

- **Believed (by everyone):** misconfiguration.
- **Measured:** 4.4 tok/s streaming through the public port with a 54 GB BF16 checkpoint.
- **Arithmetic:** 273 GB/s ÷ 54 GB ≈ 5. It was at the ceiling. (`docs/ARCHITECTURE.md` §1, figure 1)
- **Lesson:** before tuning, compute the physical ceiling of the operation. A "slow" system at 88 % of its ceiling is not misconfigured.

## 3. First attempts that did not work

- **FP8 on the plain BF16 checkpoint:** the restart failed with `Address already in use` while the *old* process kept answering, which looked like success for several minutes.
  - Cause: `supervisorctl stop` does not stop the API-server and engine children; they keep port 18000 and the GPU.
  - First fix attempt, `pkill -f "vllm serve"`, killed my own SSH shell, because the pattern also matches the shell's own command line. The `[v]llm` bracket trick fixes it.
  - **Lesson:** verify that the *new* process is the one answering (PID, log line, quantisation flag), not just that something answers.
- **GGUF (Q4/Q6):** the installed vLLM rejects GGUF at the loader; the file that had been downloaded was useless and was deleted from the billed disk.

## 4. Research and the recipe

Searching for how others run this model on this GPU found measured numbers for NVFP4 (~11.5 tok/s single stream, published) and a speculative-decoding drafter reporting 54 tok/s on the same hardware family. Those numbers were for the base model, not the abliterated one; the drafter was trained against the base weights, and abliteration is a small weight edit, so high acceptance was plausible but unproven. (`05-sources.md`)

## 5. Result: 35–41 tok/s

Deploying an abliterated NVFP4 checkpoint plus the drafter gave 41 tok/s on code and a time to first token of 0.75–2.2 s in the first measurements, later ~35 with the final configuration. The requested target (first token in 1–3 s, more than 40 tok/s) is met for short code prompts and not for free prose (~14 tok/s), which the article must say plainly.

## 6. The public endpoint

- A DNS A record cannot carry a port and the provider's ports change on recreation, so the name points at an existing edge VPS running Traefik, which forwards to the GPU. Let's Encrypt issued the certificate on first request.
- The provider's edge wants a per-instance token. Instead of handing it to clients, Traefik injects it as a cookie and vLLM enforces one stable key on the `Authorization` header.
- **Mistake:** my first Traefik update produced a router with the same name as the service; the second block-replacement removed the first. Two managed blocks sharing a name in different YAML sections collide in a name-based regex. Fixed by naming the router differently, then verified by parsing the YAML on the server.

## 7. The failure the short benchmarks could not see

- The first full test suite passed every short test and failed one needle-in-a-haystack test at ~28k tokens with degenerate output (`duct Register Register …`). (`reports/runs/run-1.json`)
- A follow-up series between 22k and 26k tokens failed most attempts (that series was typed interactively and not saved; it was later reproduced with saved data — `reports/longctx/longctx-fp8kv-experiment.json`).
- Cause: fp8 KV cache. Switching the KV cache to bf16 removed the failures (15/15 in the suite, 8/8 up to ~140k tokens). The cache still holds ~400k tokens in bf16, so the memory saving had never been needed.
- **Lesson:** include long-prompt tests in the first suite, and run them at the sizes you intend to serve.

## 8. FP8 versus NVFP4

- **Estimate before measuring:** NVFP4 faster (fewer bytes).
- **Measured:** equal single-stream speed with speculative decoding, FP8 faster on prefill and aggregate throughput. (`02-facts`)
- **Why (hypothesis, not proven):** the drafter moves the workload from bandwidth toward compute, so weight bytes matter less.
- **Lesson:** estimates from a model of the bottleneck are only valid while that bottleneck is the binding one.

## 9. Measuring quality, and the grader that was wrong

- First quality numbers for the Qwen models were 155/164 (HumanEval) and 191/200 (GSM8K).
- Reading the failures showed harness bugs, not model bugs: helper functions defined in the prompt were not prepended (4 `NameError`s), and 6 GSM8K answers were cut at 1024 tokens and had no answer at all.
- Earlier, in the small suite, the thinking test reported a failure that was the model's own arithmetic slip (14:20 + 2 h 55 min is 17:15; the model said 16:55). The test itself was flawed, though: it tied the parser contract (reasoning separated when thinking is on) to answer accuracy, so one wrong answer failed the contract. The two questions were separated.
- The grader was fixed, validated in both directions (a correct solution passes, a wrong one fails), and the old result was kept under a flagged name.
- **The surprise:** the re-run with the fixed grader gave 154/164 and 194/200, i.e. -1 and +3 against the first run, not the +4 and +6 the bug counts suggested. New failures appeared where old ones disappeared: at temperature 0 the outputs are not bit-identical between runs (batching, speculative decoding), and the run-to-run variation is a few problems. Totals alone cannot support a 2-point claim.
- **Lesson:** grade the grader first. A number from a harness you have not tried to break is a hypothesis.

## 10. A yardstick

Claude Code in non-interactive mode gave a clean, cheap reference: same prompts, same grader, no tools (`claude -p --model claude-sonnet-5 --effort medium --tools ""`). 164/164 and 197/200 for US$ 2.90. Paired significance then showed which differences were real. The first attempt to run the yardstick at scale used a default configuration that loaded 43 000 tokens of context per call; disabling settings sources brought it to 3 700.

## 11. Things that broke while experimenting

- **The provider dropped the deploy key** from `authorized_keys` (it regenerates that file from account-level keys). The experiment queue kept running and would have benchmarked the *previous* model under the new label. Fixed by registering the key on the account, and by making the queue verify that a deploy succeeded and that the expected quantisation appears in the server log.
- **Two benchmark processes were running at once** for a while (a `nohup` job outlived its shell), so one run's numbers were contaminated; both were killed and the run repeated.
- **`configure` restarted a server that was still loading its weights**, found by the idempotency test: a server that is starting looks unhealthy for several minutes. Fixed by waiting for the health check when the configuration already matches.
- **Test harness quirks:** Windows `bash` resolved to the WSL launcher; provider login banners polluted command output and broke an equality check; a Python heredoc silently changed escape sequences and produced files with real newlines inside string literals. Each failure was a *test* bug, confirmed by reading the data before "fixing" the system.

## 12. Making it repeatable

Idempotent scripts, Terraform whose `plan` after `apply` is empty, a rebuild workflow, and 16 idempotency checks including a real provider stop/start (472 s until the API answered again, same address, weights intact, Whisper back on its own 29 s later). A speech-to-text sidecar completed the modalities (image worked out of the box; audio needed a second model). (`reports/ops/`)

## 13. An unsupported number, caught by audit

While assembling this dossier a headline figure ("12 of 36 needle tests failed with the fp8 KV cache") turned out not to be backed by any saved file: the saved run had one failure in five, and the larger series had only been typed interactively. The claim was removed everywhere and the experiment re-run so that the number exists in `reports/`. **Lesson:** a claim is only as good as the file that contains it; audit the ledger before publishing (`06-claims-ledger.md`).

## 14. What was deliberately not done

A test of the model's refusal behaviour was requested and not built: it would have required writing prompts designed to elicit dangerous content, which I do not produce. The abliteration rests on the publisher's model card and the shared lineage of the weights, and the repository says so.
