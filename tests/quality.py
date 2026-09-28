#!/usr/bin/env python3
"""Quality benchmarks against the OpenAI-compatible endpoint (stdlib only).

  HumanEval (164, pass@1, greedy)   - code correctness, executed locally with timeouts
  GSM8K     (first N, exact match)  - arithmetic word-problem reasoning
  fidelity  (fixed prompts, greedy) - saved outputs to compare variants token-by-token (tests/fidelity_diff.py)

usage: BASE_URL API_KEY python tests/quality.py <variant-name> [--n-gsm 200] [--think]
writes reports/quality/quality-<variant>.json (+ reports/quality/fidelity-<variant>.json)
"""
import argparse, concurrent.futures as cf, gzip, io, json, os, re, subprocess, sys, tempfile, time, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import suite  # noqa: E402

HE_URL = "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
GSM_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
CACHE = os.path.join(os.path.dirname(__file__), ".cache"); os.makedirs(CACHE, exist_ok=True)


def fetch(url, name):
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        open(p, "wb").write(urllib.request.urlopen(url, timeout=60).read())
    return open(p, "rb").read()


def code_of(text):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def run_he(prob, think):
    prompt = ("Complete the following Python function. Return the complete function (including the signature) in one ```python code block, "
              "without tests or explanation.\n\n```python\n" + prob["prompt"] + "```")
    r = suite.call([{"role": "user", "content": prompt}], think=think, max_tokens=4000 if think else 2048)
    if r["status"] != 200:
        return {"id": prob["task_id"], "pass": False, "err": f"http {r['status']}"}
    code = code_of(r["text"])
    # Standard HumanEval protocol: the prompt (imports, helper functions, signature + docstring) always comes first and the
    # model's code follows, redefining the target function. A body-only answer is indented under the signature.
    if f"def {prob['entry_point']}" not in code:
        code = "\n".join("    " + l for l in code.splitlines())
    src = prob["prompt"] + "\n" + code + "\n\n" + prob["test"] + f"\n\ncheck({prob['entry_point']})\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        p = subprocess.run([sys.executable, f.name], capture_output=True, timeout=15, text=True, encoding="utf-8", errors="replace")
        ok, err = p.returncode == 0, p.stderr[-160:]
    except subprocess.TimeoutExpired:
        ok, err = False, "timeout"
    os.unlink(f.name)
    return {"id": prob["task_id"], "pass": ok, "err": "" if ok else err, "tokens": r.get("tokens")}


def run_gsm(item, think):
    q = item["question"]; gold = item["answer"].split("####")[-1].strip().replace(",", "")
    r = suite.call([{"role": "user", "content": q + "\n\nSolve step by step, then finish with a last line exactly: ANSWER: <number>"}],
                   think=think, max_tokens=4000 if think else 2048)
    m = re.findall(r"ANSWER:\s*\$?(-?[\d,]*\.?\d+)", r.get("text") or "")
    got = m[-1].replace(",", "") if m else None
    try:
        ok = got is not None and abs(float(got) - float(gold)) < 1e-6
    except ValueError:
        ok = False
    return {"pass": ok, "gold": gold, "got": got, "tokens": r.get("tokens")}


FIDELITY_PROMPTS = [
    "Explique a diferenca entre processo e thread em 5 frases.",
    "Escreva uma funcao Python que valida um CPF.",
    "Resuma o que e o teorema CAP em um paragrafo.",
    "Liste 8 boas praticas para APIs REST, uma por linha.",
    "Escreva uma query SQL que retorna o segundo maior salario por departamento.",
    "Traduza para ingles: 'A latencia do modelo depende da largura de banda da memoria.'",
    "Escreva um script bash que faz backup incremental de uma pasta com rsync.",
    "Explique o que e quantizacao de modelos de linguagem para um engenheiro de software.",
    "Escreva em Python uma busca binaria recursiva com type hints e docstring.",
    "Qual a complexidade de quicksort no pior caso e por que?",
    "Escreva um regex que valida um e-mail simples e explique cada parte.",
    "Descreva os passos de um deploy azul-verde.",
]


def fidelity(think):
    out = []
    for p in FIDELITY_PROMPTS:
        r = suite.call([{"role": "user", "content": p}], think=think, max_tokens=400)
        out.append({"prompt": p, "text": r.get("text") or ""})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("variant"); ap.add_argument("--n-gsm", type=int, default=200); ap.add_argument("--think", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    he = [json.loads(l) for l in gzip.decompress(fetch(HE_URL, "HumanEval.jsonl.gz")).decode().splitlines()]
    gsm = [json.loads(l) for l in fetch(GSM_URL, "gsm8k_test.jsonl").decode().splitlines()][:a.n_gsm]
    with cf.ThreadPoolExecutor(a.workers) as ex:
        he_res = list(ex.map(lambda p: run_he(p, a.think), he))
        print(f"humaneval {sum(r['pass'] for r in he_res)}/{len(he_res)}  ({time.time() - t0:.0f}s)", flush=True)
        gsm_res = list(ex.map(lambda i: run_gsm(i, a.think), gsm))
        print(f"gsm8k {sum(r['pass'] for r in gsm_res)}/{len(gsm_res)}  ({time.time() - t0:.0f}s)", flush=True)
    os.makedirs("reports/quality", exist_ok=True)
    json.dump({"variant": a.variant, "think": a.think, "humaneval": {"passed": sum(r["pass"] for r in he_res), "total": len(he_res), "rows": he_res},
               "gsm8k": {"passed": sum(r["pass"] for r in gsm_res), "total": len(gsm_res), "rows": gsm_res}, "seconds": round(time.time() - t0)},
              open(f"reports/quality/quality-{a.variant}.json", "w"), indent=1)
    json.dump(fidelity(a.think), open(f"reports/quality/fidelity-{a.variant}.json", "w"), indent=1, ensure_ascii=False)
    print("saved quality + fidelity for", a.variant)
