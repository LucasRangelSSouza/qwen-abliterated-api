import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.gpu.controller import load_onstart, render_env
from app.config import Settings


def test_onstart_embute_o_vigia_e_e_bash_valido():
    t = load_onstart()
    assert t.startswith("#!/bin/bash") and "#__WATCHDOG__" not in t
    assert "WATCHDOG_EOF" in t and "vllm:request_success_total" in t and "console.vast.ai" in t
    assert "--tensor-parallel-size 2" in t and "--mamba-ssm-cache-dtype bfloat16" in t
    assert len(t.encode()) < 6500, f"onstart grande demais para o campo da Vast: {len(t.encode())} bytes"
    bash = shutil.which("bash")
    if bash:
        f = Path(tempfile.mkdtemp()) / "onstart.sh"
        f.write_bytes(t.encode("utf-8"))
        assert subprocess.run([bash, "-n", str(f)]).returncode == 0


def test_env_do_conteiner_reflete_a_configuracao():
    s = Settings(embed_mode="gpu", llm_util=0.92)
    e = render_env(s)
    assert e["EMBED_MODE"] == "gpu" and e["LLM_UTIL"] == "0.68" and e["EMBED_UTIL"] == "0.24"  # com embedding a LLM encolhe
    assert render_env(Settings())["LLM_UTIL"] == "0.92" and render_env(Settings())["EMBED_MODE"] == "off"
