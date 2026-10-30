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
    assert "--enable-auto-tool-choice" in t and "--tool-call-parser" in t  # o Hermes envia tools com tool_choice=auto
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
    assert render_env(Settings())["TOOL_PARSER"] == "qwen3_xml"
    assert render_env(Settings())["LLM_UTIL"] == "0.92" and render_env(Settings())["EMBED_MODE"] == "off"


def test_comando_do_vllm_tem_continuacao_de_linha_valida():
    """Regressao de 30/10: um \n literal no lugar da quebra de linha entregou o argumento 'n' ao vLLM e a GPU nunca ficou saudavel."""
    lines = load_onstart().splitlines()
    i = next(k for k, line in enumerate(lines) if "--enable-auto-tool-choice" in line)
    prev = lines[i - 1]
    bs_n = chr(92) + "n"  # barra + n, a sequencia de dois caracteres que nao pode aparecer no comando
    assert prev.rstrip().endswith(chr(92)) and bs_n not in prev, prev
    for k in range(i - 6, i + 1):
        assert bs_n not in lines[k], lines[k]


def test_o_teste_acima_pegaria_o_arquivo_quebrado():
    bs_n = chr(92) + "n"
    quebrado = "vllm serve x --mamba-cache-mode align " + chr(92) + "n    --enable-auto-tool-choice"
    assert bs_n in quebrado  # o mesmo criterio acusa o defeito de 30/10
