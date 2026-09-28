#!/usr/bin/env python3
"""Regenerate the article dossier's data-driven parts from reports/*.json:
  figures/*.png + figures/*.csv   (the five figures)
  02-facts-and-numbers.md         (every number with the file it came from)
usage (from the repo root): python docs/article/build.py
Missing inputs are skipped and reported, never invented."""
import csv, json, math, os, statistics as st

import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures"); os.makedirs(FIG, exist_ok=True)
GPU_USD_H = 0.449
C_SONNET, C_FP8, C_NVFP4, C_BF16 = "#3B4252", "#1B9E77", "#D95F02", "#7570B3"
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "font.size": 10, "axes.titleweight": "bold", "figure.dpi": 110})


def load(rel):
    p = os.path.join(ROOT, "reports", rel)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def csv_out(name, header, rows):
    with open(os.path.join(FIG, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; ctr = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (ctr - h), 100 * (ctr + h)


run_nv, run_fp, run_fin = load("runs/run-2.json"), load("runs/run-fp8-160k.json"), load("runs/run-final-fp8.json")
q = {"Sonnet 5 medium": load("quality/quality-sonnet-5-medium.json"), "Qwen FP8": load("quality/quality-fp8.json"), "Qwen NVFP4": load("quality/quality-nvfp4.json")}
lc = {"NVFP4": load("longctx/longctx-nvfp4-160k.json"), "FP8": load("longctx/longctx-fp8-160k.json")}
kv_fp8, kv_bf16 = load("longctx/longctx-fp8kv-experiment.json"), load("longctx/longctx-kv-auto.json")
idem, pipe = load("ops/idempotency.json"), load("ops/pipeline-audio.json")
made, skipped = [], []

# ---- figure 1: bandwidth ceiling vs measured
if run_fin and run_nv:
    BW = 273.0
    rows = [("BF16", BW / 54, 4.4, None, "ceiling = 273 GB/s / 54 GB; measured 4.4 tok/s (reports/runs/run-1.log era, BF16 checkpoint)"),
            ("FP8", BW / 27, None, run_fin["sections"]["speed"]["code_think=False"]["decode_tps"]["mean"], "plain FP8 not measured; with DFlash2 = final run, code"),
            ("NVFP4", BW / 20, 11.5, run_nv["sections"]["speed"]["code_think=False"]["decode_tps"]["mean"], "plain 11.5 tok/s is PUBLISHED (NVIDIA forum), not measured here; with DFlash2 = run-2, code")]
    fig, ax = plt.subplots(figsize=(7.2, 4))
    x = range(len(rows)); w = 0.26
    ax.bar([i - w for i in x], [r[1] for r in rows], w, color="#BBBBBB", label="theoretical ceiling (bandwidth / weight bytes)")
    ax.bar([i for i in x], [r[2] or 0 for r in rows], w, color=C_BF16, label="plain decoding (BF16 measured; NVFP4 published)")
    ax.bar([i + w for i in x], [r[3] or 0 for r in rows], w, color=C_FP8, label="with DFlash2 speculative decoding (measured)")
    ax.set_xticks(list(x)); ax.set_xticklabels([r[0] for r in rows]); ax.set_ylabel("decode tokens/s, single stream, code")
    ax.set_title("Decode speed is bounded by bytes read per token"); ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "fig1_bandwidth_ceiling.png")); plt.close(fig)
    csv_out("fig1_bandwidth_ceiling.csv", ["format", "ceiling_tok_s", "plain_tok_s", "speculative_tok_s", "note"], rows); made.append("fig1")
else:
    skipped.append("fig1 (needs run-2 and run-final-fp8)")

# ---- figure 2: decode by workload
if run_fin and run_nv:
    keys = ["code_think=False", "sql_think=False", "prose_think=False", "code_think=True", "sql_think=True", "prose_think=True"]
    labels = ["code\nno thinking", "SQL\nno thinking", "prose\nno thinking", "code\nthinking", "SQL\nthinking", "prose\nthinking"]
    fp = [run_fin["sections"]["speed"][k]["decode_tps"]["mean"] for k in keys]; nv = [run_nv["sections"]["speed"][k]["decode_tps"]["mean"] for k in keys]
    fig, ax = plt.subplots(figsize=(8, 4)); x = range(len(keys)); w = 0.38
    ax.bar([i - w / 2 for i in x], fp, w, color=C_FP8, label="FP8 (final run)"); ax.bar([i + w / 2 for i in x], nv, w, color=C_NVFP4, label="NVFP4 (run-2)")
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, fontsize=8); ax.set_ylabel("decode tokens/s"); ax.legend(frameon=False)
    ax.set_title("Speculative decoding helps predictable text: code and SQL vs prose")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "fig2_decode_by_workload.png")); plt.close(fig)
    csv_out("fig2_decode_by_workload.csv", ["workload", "fp8_tok_s", "nvfp4_tok_s"], list(zip([k.replace("_think=", " thinking=") for k in keys], fp, nv))); made.append("fig2")
else:
    skipped.append("fig2")

# ---- figure 3: prefill vs prompt length
if all(lc.values()):
    fig, ax = plt.subplots(figsize=(7, 4)); rows = []
    for name, col in (("FP8", C_FP8), ("NVFP4", C_NVFP4)):
        pts = {}
        for r in lc[name]:
            pts.setdefault(r["target"], []).append((r["prompt_tokens"], r["ttft"]))
        xs = sorted(pts); X = [st.mean(p for p, _ in pts[s]) / 1000 for s in xs]; Y = [st.mean(t for _, t in pts[s]) for s in xs]
        ax.plot(X, Y, "o-", color=col, label=name); rows += [(name, round(a, 1), round(b, 1)) for a, b in zip(X, Y)]
    ax.set_xlabel("prompt tokens (thousands)"); ax.set_ylabel("time to first token (s)"); ax.legend(frameon=False)
    ax.set_title("Prefill cost grows with the prompt: ~12 s at 30k, ~100 s at 140k (FP8)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "fig3_prefill_vs_context.png")); plt.close(fig)
    csv_out("fig3_prefill_vs_context.csv", ["variant", "prompt_k_tokens", "ttft_s"], rows); made.append("fig3")
else:
    skipped.append("fig3")

# ---- figure 4: quality vs cost
if all(q.values()):
    cost = {"Sonnet 5 medium": q["Sonnet 5 medium"]["usage"]["cost"] / 364 * 1000,
            "Qwen FP8": q["Qwen FP8"]["seconds"] * GPU_USD_H / 3600 / 364 * 1000, "Qwen NVFP4": q["Qwen NVFP4"]["seconds"] * GPU_USD_H / 3600 / 364 * 1000}
    fig, ax = plt.subplots(figsize=(7, 4.2)); rows = []
    for name, col in (("Sonnet 5 medium", C_SONNET), ("Qwen FP8", C_FP8), ("Qwen NVFP4", C_NVFP4)):
        k, n = q[name]["humaneval"]["passed"], q[name]["humaneval"]["total"]; lo, hi = wilson(k, n); pct = 100 * k / n
        ax.errorbar(cost[name], pct, yerr=[[pct - lo], [hi - pct]], fmt="o", color=col, capsize=4, ms=8, label=f"{name}")
        ax.annotate(name, (cost[name], pct), textcoords="offset points", xytext=(8, -12), fontsize=8)
        rows.append((name, round(cost[name], 3), k, n, round(pct, 1), round(lo, 1), round(hi, 1)))
    ax.set_xscale("log"); ax.set_xlabel("cost per 1000 tasks, US$ (log scale; GPU assumed busy)"); ax.set_ylabel("HumanEval pass@1 (%) with 95% CI")
    ax.set_xticks([0.3, 0.5, 1, 2, 4, 8]); ax.get_xaxis().set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}")); ax.minorticks_off()
    ax.set_title("Sonnet 5: ~4 points better on HumanEval, ~23x the cost per task", fontsize=10); ax.set_ylim(85, 101)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "fig4_quality_vs_cost.png")); plt.close(fig)
    csv_out("fig4_quality_vs_cost.csv", ["model", "usd_per_1000_tasks", "humaneval_passed", "humaneval_total", "pct", "ci_low", "ci_high"], rows); made.append("fig4")
else:
    skipped.append("fig4")

# ---- figure 5: fp8 vs bf16 KV cache needle retrieval, by actual prompt length
if kv_fp8 and kv_bf16:
    fig, ax = plt.subplots(figsize=(7.5, 4.2))

    def by_size(rows):
        out = {}
        for r in rows:
            out.setdefault(r["target"], []).append(r)
        return {t: (st.mean(x["prompt_tokens"] for x in v) / 1000, 100 * sum(x["ok"] for x in v) / len(v)) for t, v in sorted(out.items())}

    series = [("fp8 KV cache (FP8 weights)", "#C0392B", "o", by_size(kv_fp8)), ("bf16 KV cache (NVFP4 weights, same series)", C_NVFP4, "s", by_size(kv_bf16))]
    if run_fin:
        pts = {}
        for k, v in run_fin["sections"]["context"].items():
            if v.get("prompt_tokens"):
                pts[k] = (v["prompt_tokens"] / 1000, 100 * v["found"] / v["of"])
        series.append(("bf16 KV cache (FP8 weights, final suite)", C_FP8, "^", pts))
    rows = []
    for name, col, mk, pts in series:
        xs = [p[0] for p in pts.values()]; ys = [p[1] for p in pts.values()]
        ax.plot(xs, ys, mk + "-", color=col, label=name, ms=7, alpha=0.9)
        rows += [(name, round(a, 1), round(b, 0)) for a, b in zip(xs, ys)]
    ax.axvspan(21, 24, color="#F5B7B1", alpha=0.35, label="boundary observed with fp8 KV (21k passes, 24.5k fails)")
    ax.set_xlabel("prompt tokens (thousands)"); ax.set_ylabel("needle retrieved (% of 3 attempts)"); ax.set_ylim(-5, 108)
    ax.legend(frameon=False, fontsize=7.5, loc="lower left"); ax.set_title("fp8 KV cache: fine on short prompts, degenerate output past ~24k tokens", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "fig5_kv_cache_needle.png")); plt.close(fig)
    csv_out("fig5_kv_cache_needle.csv", ["series", "prompt_k_tokens", "needle_found_pct"], rows); made.append("fig5")
else:
    skipped.append("fig5 (run tests/queue_kv_experiment.py first)")

# ---- facts sheet
o = ["# Facts and numbers\n", "Generated by `docs/article/build.py`; every row names the file it came from. Do not edit by hand: change the data, rerun the script.\n"]
if run_fin:
    S = run_fin["sections"]
    o.append("## Speed, final run (FP8, 160k context) — `reports/runs/run-final-fp8.json`\n\n| workload | thinking | TTFT s | decode tok/s |\n|---|---|---:|---:|")
    for k, v in S["speed"].items():
        n, t = k.split("_think="); o.append(f"| {n} | {'on' if t == 'True' else 'off'} | {v['ttft']['mean']} | {v['decode_tps']['mean']} |")
    o.append("\n| clients | aggregate tok/s | per-request tok/s |\n|---|---:|---:|")
    for k, v in S["concurrency"].items():
        o.append(f"| {k[2:]} | {v['aggregate_tps']} | {v['per_req_tps'].get('mean')} |")
    o.append(f"\nLong generation: {S['longgen']['tokens']} tokens at {S['longgen']['decode_tps']:.1f} tok/s. Stability: {S['stability']['ok']}/{S['stability']['requests']} ok, p50 {S['stability']['p50']} s, p95 {S['stability']['p95']} s. Prefix cache: {S['prefix_cache']['first_ttft']:.1f} s to {S['prefix_cache']['second_ttft']:.1f} s on an {S['prefix_cache']['prompt_tokens']}-token prompt.\n")
if run_nv and run_fp:
    o.append("## Speed across the three suite runs (code, thinking off) — `reports/runs/`\n\n| run | precision | code tok/s | SQL tok/s | prose tok/s | 4 clients aggregate | 8 clients aggregate |\n|---|---|---:|---:|---:|---:|---:|")
    for nm, prec, r in (("run-2 (32k ctx)", "NVFP4", run_nv), ("run-fp8-160k", "FP8", run_fp), ("run-final-fp8", "FP8", run_fin)):
        if r:
            s_ = r["sections"]; o.append(f"| {nm} | {prec} | {s_['speed']['code_think=False']['decode_tps']['mean']} | {s_['speed']['sql_think=False']['decode_tps']['mean']} | {s_['speed']['prose_think=False']['decode_tps']['mean']} | {s_['concurrency']['c=4']['aggregate_tps']} | {s_['concurrency']['c=8']['aggregate_tps']} |")
    o.append("\nThe FP8 aggregate throughput was measured twice (98.6 and 90.6 tok/s at 4 clients): run-to-run variation is about 8 %, so the NVFP4 vs FP8 throughput gap (78.5 vs 91-99) is suggestive, not proven.\n")
if all(lc.values()):
    o.append("## Long context — `reports/longctx/longctx-{fp8,nvfp4}-160k.json`\n\n| prompt tokens | FP8 TTFT s | FP8 found | NVFP4 TTFT s | NVFP4 found |\n|---:|---:|---:|---:|---:|")
    for s in sorted({r["target"] for r in lc["FP8"]}):
        f_ = [r for r in lc["FP8"] if r["target"] == s]; n_ = [r for r in lc["NVFP4"] if r["target"] == s]
        o.append(f"| ~{round(st.mean(r['prompt_tokens'] for r in f_) / 1000)}k | {st.mean(r['ttft'] for r in f_):.0f} | {sum(r['ok'] for r in f_)}/{len(f_)} | {st.mean(r['ttft'] for r in n_):.0f} | {sum(r['ok'] for r in n_)}/{len(n_)} |")
    o.append("")
if kv_fp8:
    okp = [r["prompt_tokens"] for r in kv_fp8 if r["ok"]]; badp = [r["prompt_tokens"] for r in kv_fp8 if not r["ok"]]
    ctl = ""
    if run_fin:
        c_ = run_fin["sections"]["context"]; ctl = f" Control with FP8 weights and the bf16 KV cache (final suite, same prompt design, 3 attempts per size): " + ", ".join(f"~{round(v['prompt_tokens'] / 1000)}k {v['found']}/{v['of']}" for v in c_.values()) + "."
    o.append(
        f"## KV cache dtype experiment — `reports/longctx/longctx-fp8kv-experiment.json`\n\nfp8 KV cache (FP8 weights): needle found in {sum(r['ok'] for r in kv_fp8)}/{len(kv_fp8)} attempts across targets {sorted({r['target'] for r in kv_fp8})} tokens (3 seeds each). "
        f"Every success had a prompt of at most {max(okp) / 1000:.1f}k tokens; every failure had at least {min(badp) / 1000:.1f}k tokens, and every failed answer began with `{sorted({r['head'][:24] for r in kv_fp8 if not r['ok']})[0]}`. "
        f"bf16 KV cache on the same series (`longctx-kv-auto.json`, NVFP4 weights): {sum(r['ok'] for r in kv_bf16)}/{len(kv_bf16)}.{ctl} "
        "The boundary lies between ~21k and ~24.5k tokens for this model, prompt design and vLLM version; the earlier interactive series (not saved) suggested it is fuzzy around 22-24k.\n"
    )
if all(q.values()):
    o.append("## Quality — `reports/quality/quality-*.json`\n\n| model | HumanEval | 95% CI | GSM8K | 95% CI | run seconds |\n|---|---:|---|---:|---|---:|")
    for nm, d in q.items():
        h, g = d["humaneval"], d["gsm8k"]; hl, hh = wilson(h["passed"], h["total"]); gl, gh = wilson(g["passed"], g["total"])
        o.append(f"| {nm} | {h['passed']}/{h['total']} ({100 * h['passed'] / h['total']:.1f}%) | {hl:.0f}-{hh:.0f}% | {g['passed']}/{g['total']} ({100 * g['passed'] / g['total']:.1f}%) | {gl:.0f}-{gh:.0f}% | {d['seconds']} |")
    o.append(f"\nSonnet 5 run: {q['Sonnet 5 medium']['usage']['calls']} calls, US$ {q['Sonnet 5 medium']['usage']['cost']:.2f}, {q['Sonnet 5 medium']['usage']['out_tokens']} output tokens. Significance tests are in `docs/RESULTS.md`.\n")
if idem:
    o.append(f"## Idempotency — `reports/ops/idempotency.json`\n\n{sum(r['pass'] for r in idem['steps'])}/{len(idem['steps'])} steps passed.\n\n| step | seconds |\n|---|---:|")
    for r in idem["steps"]:
        o.append(f"| {r['step']} | {r.get('seconds') if r.get('seconds') is not None else '-'} |")
    o.append("")
if pipe:
    o.append(f"## Audio pipeline — `reports/ops/pipeline-audio.json`\n\n" + "\n".join(f"- \"{r['transcript']}\" → {r['answer'][:60]!r} (STT {r['stt_s']} s, LLM {r['llm_s']} s)" for r in pipe["cases"]) + "\n")
o.append("## Fixed facts\n\n- Hardware: NVIDIA GB10, 119 GB unified memory, ~273 GB/s. Provider price observed: US$ 0.449/h running, ~US$ 0.007/h stopped (disk).\n- Disk: BF16 52 GB, drafter 3.6 GB, Whisper 1.6 GB. First deploy ~10 min download; restart ~7 min; provider stop/start ~8 min.\n- Software: vLLM 0.30.0; models `Blackfrost-AI/Qwen3.8-27B-ABLITERATED-BF16` (FP8 online), `...-NVFP4`, `z-lab/Qwen3.8-27B-DFlash2` (7 speculative tokens), `openai/whisper-large-v3-turbo`.\n")
open(os.path.join(HERE, "02-facts-and-numbers.md"), "w", encoding="utf-8").write("\n".join(o))
print("figures:", made, "| skipped:", skipped)
