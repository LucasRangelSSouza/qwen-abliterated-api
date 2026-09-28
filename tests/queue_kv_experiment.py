#!/usr/bin/env python3
"""Reproduce the fp8-KV-cache long-context corruption with saved data, then restore the default profile.

Deploys KV_DTYPE=fp8_e4m3, runs the same needle series used for the bf16 KV cache (16k-30k tokens, 3 seeds each),
then redeploys the default profile. env: SSH_TARGET, BASE_URL, API_KEY. Output: reports/longctx/longctx-fp8kv-experiment.json
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import queue_variants as q

SIZES = [16000, 22000, 26000, 30000]
q.log("== kv experiment: fp8 KV cache")
if q.deploy("fp8kv-experiment", {"KV_DTYPE": "fp8_e4m3"}):
    q.run([sys.executable, "-u", "tests/longctx.py", *[str(s) for s in SIZES]],
          {"OUT": "reports/longctx/longctx-fp8kv-experiment.json", "SEEDS": "3"})
else:
    q.log("!! fp8 KV deploy failed")
q.log("== kv experiment: restoring default (bf16 KV)")
q.deploy("default-restore", {})
q.log("KV EXPERIMENT DONE")
