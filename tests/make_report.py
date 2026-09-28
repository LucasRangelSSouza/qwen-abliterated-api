#!/usr/bin/env python3
"""Turn tests/suite.py JSON output (+ optional idempotency JSON) into a Markdown report.
usage: python tests/make_report.py reports/runs/run.json [reports/ops/idempotency.json] > reports/runs/REPORT.md
"""
import json, sys

run = json.load(open(sys.argv[1]))
idem = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
S = run["sections"]
f = lambda x, d=1: "-" if x is None else f"{x:.{d}f}"
o = []
o.append(f"# Test report\n\nEndpoint: `{run['base_url']}` · started {run['started']}\n")

if "auth" in S:
    a = S["auth"]
    o.append(f"## Authentication\n\n| case | HTTP |\n|---|---|\n| no key | {a['no_key']} |\n| wrong key | {a['wrong_key']} |\n| right key | {a['right_key']} |\n\nResult: **{'PASS' if a['pass'] else 'FAIL'}**\n")

if "speed" in S:
    o.append("## Speed (streaming, 3 runs each, 700 max tokens)\n\n| workload | thinking | TTFT s (mean) | decode tok/s (mean) | min-max tok/s | errors |\n|---|---|---:|---:|---|---:|")
    for k, v in S["speed"].items():
        name, think = k.split("_think=")
        d = v["decode_tps"]
        o.append(f"| {name} | {'on' if think == 'True' else 'off'} | {f(v['ttft'].get('mean'), 2)} | {f(d.get('mean'))} | {f(d.get('min'))}-{f(d.get('max'))} | {v['errors']} |")
    o.append("")

if "context" in S:
    o.append("## Heavy context (needle in a haystack, needle at 50%)\n\n| target | prompt tokens | TTFT s | needle found |\n|---|---:|---:|---|")
    for k, v in S["context"].items():
        o.append(f"| {k} | {v.get('prompt_tokens') or '-'} | {f(v.get('ttft'), 2)} | {v['found']}/{v['of']} |")
    o.append("")

if "thinking" in S:
    t = S["thinking"]
    o.append("## Thinking on/off\n")
    for k in ("think=False", "think=True"):
        rows = t[k]
        o.append(f"- **{k}**: correct {sum(r['ok'] for r in rows)}/{len(rows)}, reasoning chars {[r['reasoning_chars'] for r in rows]}, mean tokens {f(sum(r['tokens'] or 0 for r in rows) / len(rows), 0)}, mean total {f(sum(r['total'] or 0 for r in rows) / len(rows), 1)} s")
    o.append(f"\nReasoning parser separates `reasoning` from `content`: **{'PASS' if t['pass'] else 'FAIL'}**\n")

if "coding" in S:
    o.append("## Coding (10 tasks, code executed locally against asserts)\n")
    for k, v in S["coding"].items():
        o.append(f"### {k}: {v['passed']}/{v['total']}\n\n| task | pass | tokens | tok/s |\n|---|---|---:|---:|")
        for r in v["rows"]:
            o.append(f"| {r['task']} | {'yes' if r['pass'] else 'NO'} | {r['tokens'] or '-'} | {f(r['decode_tps'])} |")
        o.append("")

if "tools" in S:
    o.append(f"## Tool calling\n\nauto tool choice returns `get_weather(city=São Paulo)`: **{'PASS' if S['tools']['pass'] else 'FAIL'}**\n")

if "concurrency" in S:
    o.append("## Concurrency\n\n| clients | ok | aggregate tok/s | per-request tok/s (mean) | TTFT s (mean) | wall s |\n|---|---:|---:|---:|---:|---:|")
    for k, v in S["concurrency"].items():
        o.append(f"| {k[2:]} | {v['ok']} | {v['aggregate_tps']} | {f(v['per_req_tps'].get('mean'))} | {f(v['ttft'].get('mean'), 2)} | {v['wall']} |")
    o.append("")

if "longgen" in S:
    l = S["longgen"]
    o.append(f"## Long generation\n\n{l.get('tokens')} tokens, decode {f(l.get('decode_tps'))} tok/s, TTFT {f(l.get('ttft'), 2)} s, finish `{l.get('finish')}`\n")

if "prefix_cache" in S:
    p = S["prefix_cache"]
    o.append(f"## Prefix cache\n\nSame {p['prompt_tokens']}-token prompt twice: TTFT {f(p['first_ttft'], 2)} s → {f(p['second_ttft'], 2)} s: **{'PASS' if p['pass'] else 'no speed-up'}**\n")

if "default_thinking" in S:
    d = S["default_thinking"]
    o.append(f"## Default thinking (client sends nothing)\n\nreasoning present: **{'yes' if d['reasoning_chars'] else 'no'}** ({d['reasoning_chars']} chars), TTFT {f(d['ttft'], 2)} s, content starts `{d['content']!r}`\n")

if any(k in S for k in ("image", "audio", "transcription", "parallel_qa")):
    o.append("## Vision, audio and parallel questions\n\n| check | result | detail |\n|---|---|---|")
    if "image" in S:
        o.append(f"| image_url (red square left, blue circle right) | {'PASS' if S['image']['pass'] else 'FAIL'} | {S['image']['answer'][:90]} |")
    if "audio" in S:
        o.append(f"| audio input rejected cleanly, server keeps serving | {'PASS' if S['audio']['pass'] else 'FAIL'} | HTTP {S['audio']['status']} |")
    if "transcription" in S:
        o.append(f"| speech-to-text sidecar `/v1/audio/transcriptions` (pt-BR fixture) | {'PASS' if S['transcription']['pass'] else 'FAIL'} | {S['transcription']['text'].strip()[:80]} ({S['transcription']['seconds']} s) |")
    if "parallel_qa" in S:
        p = S["parallel_qa"]
        o.append(f"| 16 different questions at once, no cross-talk | {'PASS' if p['pass'] else 'FAIL'} | {p['correct']}/{p['of']} correct in {p['wall']} s |")
    o.append("")

if "stability" in S:
    s = S["stability"]
    o.append(f"## Stability\n\n{s['ok']}/{s['requests']} requests OK (4 parallel), p50 {s['p50']} s, p95 {s['p95']} s\n")

if idem:
    o.append("## Idempotency\n\n| step | result | seconds | detail |\n|---|---|---:|---|")
    for r in idem["steps"]:
        o.append(f"| {r['step']} | {'PASS' if r['pass'] else 'FAIL'} | {r.get('seconds', '-')} | {r.get('detail', '')} |")
    o.append("")

print("\n".join(o))
