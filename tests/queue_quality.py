#!/usr/bin/env python3
"""Quality queue: HumanEval + GSM8K + fidelity per variant, current variant first, then the other one.
usage: python tests/queue_quality.py <currently-deployed: fp8|nvfp4>"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import queue_variants as q

VARIANTS = {"fp8": ("fp8-160k", {"TARGET_DIR": "/workspace/models/bf16", "QUANT": "fp8", "MAX_LEN": "160000"}),
            "nvfp4": ("nvfp4-160k", {"MAX_LEN": "160000"})}
first = sys.argv[1]
order = [first] + [v for v in VARIANTS if v != first]
for i, v in enumerate(order):
    if i > 0 and not q.deploy(*VARIANTS[v]):
        q.log(f"!! could not deploy {v}"); continue
    q.run([sys.executable, "-u", "tests/quality.py", v])
q.run([sys.executable, "tests/fidelity_diff.py", "reports/fidelity-nvfp4.json", "reports/fidelity-fp8.json"])
q.log("QUALITY QUEUE DONE")
