#!/usr/bin/env python3
"""Compare greedy outputs of two variants: exact match, common-prefix share, similarity ratio.
usage: python tests/fidelity_diff.py reports/quality/fidelity-A.json reports/quality/fidelity-B.json"""
import difflib, json, sys

a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
rows = []
for x, y in zip(a, b):
    t1, t2 = x["text"], y["text"]
    n = 0
    while n < min(len(t1), len(t2)) and t1[n] == t2[n]:
        n += 1
    rows.append({"exact": t1 == t2, "prefix_share": n / max(len(t1), len(t2), 1), "ratio": difflib.SequenceMatcher(None, t1, t2).ratio()})
print(f"identical outputs: {sum(r['exact'] for r in rows)}/{len(rows)}")
print(f"mean common-prefix share: {sum(r['prefix_share'] for r in rows) / len(rows):.2f}")
print(f"mean similarity ratio:    {sum(r['ratio'] for r in rows) / len(rows):.2f}")
