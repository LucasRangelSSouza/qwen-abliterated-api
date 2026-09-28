#!/usr/bin/env python3
"""End-to-end test + benchmark suite for the OpenAI-compatible Qwen endpoint.

stdlib only. Usage:
  BASE_URL=https://qwen.rangeltech.net/v1 API_KEY=sk-... python tests/suite.py [--out reports/run.json] [--only auth,speed,...]

Every section records raw numbers into a JSON file; scripts/make-report.py turns it into Markdown.
"""
import argparse, concurrent.futures as cf, json, os, random, re, statistics, subprocess, sys, tempfile, textwrap, time, urllib.error, urllib.request

BASE = os.environ.get("BASE_URL", "https://qwen.rangeltech.net/v1").rstrip("/")
KEY = os.environ.get("API_KEY", "")
MODEL = os.environ.get("MODEL", "qwen-abliterated")


def call(messages, *, think=False, max_tokens=512, stream=True, temperature=0, tools=None, key=None, extra=None, timeout=900):
    """One chat completion. Returns dict with ttft, decode tok/s, text, reasoning, usage, status."""
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": think}}
    if think is None:
        body.pop("chat_template_kwargs")
    if stream:
        body.update(stream=True, stream_options={"include_usage": True})
    if tools:
        body["tools"] = tools
    if extra:
        body.update(extra)
    req = urllib.request.Request(BASE + "/chat/completions", json.dumps(body).encode(),
                                 {"Authorization": "Bearer " + (KEY if key is None else key), "Content-Type": "application/json"})
    t0 = time.time(); first = None; text = ""; reasoning = ""; usage = {}; calls = {}; finish = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:  # network
        return {"status": 0, "error": repr(e)}
    if not stream:
        d = json.load(resp); m = d["choices"][0]["message"]
        dt = time.time() - t0
        u = d.get("usage", {})
        return {"status": 200, "total": dt, "text": m.get("content") or "", "reasoning": m.get("reasoning") or m.get("reasoning_content") or "",
                "usage": u, "tool_calls": m.get("tool_calls"), "finish": d["choices"][0].get("finish_reason")}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: ") or line.endswith("[DONE]"):
            continue
        d = json.loads(line[6:])
        if d.get("usage"):
            usage = d["usage"]
        if d.get("choices"):
            ch = d["choices"][0]; dl = ch.get("delta", {})
            c = dl.get("content") or ""; r = dl.get("reasoning") or dl.get("reasoning_content") or ""
            for tc in dl.get("tool_calls") or []:
                calls.setdefault(tc["index"], {"name": "", "arguments": ""})
                f = tc.get("function", {}); calls[tc["index"]]["name"] += f.get("name") or ""; calls[tc["index"]]["arguments"] += f.get("arguments") or ""
                if first is None:
                    first = time.time() - t0
            if (c or r) and first is None:
                first = time.time() - t0
            text += c; reasoning += r
            finish = ch.get("finish_reason") or finish
    total = time.time() - t0
    n = usage.get("completion_tokens", 0)
    dec = (n - 1) / (total - first) if first and n > 1 and total > first else None
    return {"status": 200, "ttft": first, "total": total, "tokens": n, "prompt_tokens": usage.get("prompt_tokens"),
            "decode_tps": dec, "text": text, "reasoning": reasoning, "tool_calls": list(calls.values()) or None, "finish": finish}


def summ(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "mean": round(statistics.mean(xs), 2), "min": round(min(xs), 2), "max": round(max(xs), 2)} if xs else {"n": 0}


# ---------------------------------------------------------------- sections
def t_auth():
    out = {}
    req = urllib.request.Request(BASE + "/models")
    for label, key in (("no_key", None), ("wrong_key", "sk-wrong"), ("right_key", KEY)):
        r = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + key} if key else {})
        try:
            out[label] = urllib.request.urlopen(r, timeout=30).status
        except urllib.error.HTTPError as e:
            out[label] = e.code
    out["pass"] = out["no_key"] == 401 and out["wrong_key"] == 401 and out["right_key"] == 200
    return out


def t_models():
    r = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + KEY})
    d = json.load(urllib.request.urlopen(r, timeout=30))
    m = d["data"][0]
    return {"id": m["id"], "max_model_len": m.get("max_model_len"), "pass": m["id"] == MODEL}


def t_speed():
    """Short/medium generation, thinking on and off, 3 runs each, streaming."""
    out = {}
    prompts = {
        "code": "Escreva em Python uma classe LRUCache com get/put O(1) e testes pytest.",
        "prose": "Explique em 3 paragrafos como funciona o protocolo TLS 1.3.",
        "sql": "Escreva uma query SQL (BigQuery) que calcula retencao D1/D7/D30 por coorte semanal a partir de events(user_id, ts).",
    }
    for think in (False, True):
        for name, p in prompts.items():
            runs = [call([{"role": "user", "content": p}], think=think, max_tokens=700) for _ in range(3)]
            ok = [r for r in runs if r["status"] == 200]
            out[f"{name}_think={think}"] = {"ttft": summ([r["ttft"] for r in ok]), "decode_tps": summ([r["decode_tps"] for r in ok]),
                                          "tokens": summ([r["tokens"] for r in ok]), "errors": len(runs) - len(ok)}
    return out


def filler(n_tokens, seed=1):
    rnd = random.Random(seed)
    words = "servidor cache fila latencia indice tabela contrato escola municipio lote saldo fatura vetor grafo sessao token modelo deploy rollback".split()
    return " ".join(rnd.choice(words) for _ in range(int(n_tokens / 1.3)))


def t_context():
    """Needle-in-a-haystack at growing prompt sizes; measures prefill (TTFT) and retrieval."""
    out = {}
    for target in (2000, 8000, 16000, 24000, 30000):
        rows = []
        for seed in (1, 2, 3):
            needle = f"CODIGO-SECRETO-{random.Random(target + seed).randint(10000, 99999)}"
            hay = filler(target, seed); pos = len(hay) // 2
            hay = hay[:pos] + f"\n\nA senha do cofre e {needle}.\n\n" + hay[pos:]
            r = call([{"role": "user", "content": hay + "\n\nQual e a senha do cofre? Responda so com a senha."}], think=False, max_tokens=40)
            rows.append({"prompt_tokens": r.get("prompt_tokens"), "ttft": r.get("ttft"), "found": needle in (r.get("text") or "")})
        out[f"~{target}"] = {"prompt_tokens": rows[0]["prompt_tokens"], "ttft": statistics.mean(x["ttft"] for x in rows if x["ttft"]),
                             "found": sum(x["found"] for x in rows), "of": len(rows)}
    return out


def t_thinking():
    qs = [("Quanto e 17*23? Responda so o numero.", "391"),
          ("Se um trem sai as 14:20 e viaja 2h55min, que horas chega? Responda HH:MM.", "17:15"),
          ("Qual a soma dos inteiros de 1 a 100? Responda so o numero.", "5050")]
    out = {}
    for think in (False, True):
        rows = []
        for q, ans in qs:
            r = call([{"role": "user", "content": q}], think=think, max_tokens=1500)
            rows.append({"ok": ans in (r.get("text") or ""), "reasoning_chars": len(r.get("reasoning") or ""), "ttft": r.get("ttft"),
                         "total": r.get("total"), "tokens": r.get("tokens")})
        out[f"think={think}"] = rows
    # "pass" is about the parser contract (reasoning separated when on, absent when off); answer accuracy is reported per mode.
    out["pass"] = any(x["reasoning_chars"] > 0 for x in out["think=True"]) and all(x["reasoning_chars"] == 0 for x in out["think=False"])
    return out


CODING = [  # (name, prompt, test code appended after the model's function). Executed locally with python.
    ("fizzbuzz", "Escreva em Python a funcao fizzbuzz(n) que retorna lista de strings de 1..n.",
     "assert fizzbuzz(15)[-1]=='FizzBuzz' and fizzbuzz(5)==['1','2','Fizz','4','Buzz']"),
    ("is_palindrome", "Escreva em Python is_palindrome(s) ignorando espacos, pontuacao e maiusculas.",
     "assert is_palindrome('A man, a plan, a canal: Panama') and not is_palindrome('abc')"),
    ("merge_intervals", "Escreva em Python merge_intervals(intervals) que une intervalos sobrepostos (lista de [a,b]) e retorna ordenado.",
     "assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]] and merge_intervals([[1,4],[4,5]])==[[1,5]]"),
    ("lru", "Escreva em Python a classe LRUCache(capacity) com get(key)->-1 se ausente e put(key,value), O(1).",
     "c=LRUCache(2);c.put(1,1);c.put(2,2);assert c.get(1)==1;c.put(3,3);assert c.get(2)==-1;c.put(4,4);assert c.get(1)==-1 and c.get(3)==3 and c.get(4)==4"),
    ("topo_sort", "Escreva em Python topo_sort(graph) com graph dict no->lista de dependentes; retorna ordem topologica ou levanta ValueError se ha ciclo.",
     "assert topo_sort({'a':['b'],'b':['c'],'c':[]})==['a','b','c']\ntry:\n    topo_sort({'a':['b'],'b':['a']}); raise SystemExit('no error')\nexcept ValueError: pass"),
    ("roman", "Escreva em Python int_to_roman(n) para 1..3999.",
     "assert int_to_roman(1994)=='MCMXCIV' and int_to_roman(58)=='LVIII' and int_to_roman(3999)=='MMMCMXCIX'"),
    ("wordfreq", "Escreva em Python top_k_words(text,k) retornando lista de (palavra,contagem), minusculas, desempate alfabetico.",
     "assert top_k_words('a b a c b a',2)==[('a',3),('b',2)] and top_k_words('x y',5)==[('x',1),('y',1)]"),
    ("lis", "Escreva em Python lis_length(nums) com o tamanho da maior subsequencia crescente estrita em O(n log n).",
     "assert lis_length([10,9,2,5,3,7,101,18])==4 and lis_length([])==0 and lis_length([7,7,7])==1"),
    ("parse_duration", "Escreva em Python parse_duration(s) convertendo '1h30m15s', '45s', '2h' em segundos (int).",
     "assert parse_duration('1h30m15s')==5415 and parse_duration('45s')==45 and parse_duration('2h')==7200"),
    ("matrix_spiral", "Escreva em Python spiral(matrix) retornando os elementos em ordem espiral horaria.",
     "assert spiral([[1,2,3],[4,5,6],[7,8,9]])==[1,2,3,6,9,8,7,4,5] and spiral([[1],[2]])==[1,2]"),
]


def extract_code(text):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    return "\n\n".join(blocks) if blocks else text


def t_coding():
    out = {}
    for think in (False, True):
        rows = []
        for name, prompt, test in CODING:
            r = call([{"role": "system", "content": "Responda apenas com um bloco de codigo Python, sem explicacao."}, {"role": "user", "content": prompt}],
                     think=think, max_tokens=4000 if think else 1200)
            ok = False; err = ""
            if r["status"] == 200:
                src = extract_code(r["text"]) + "\n\n" + test + "\n"
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
                    f.write(src)
                try:
                    p = subprocess.run([sys.executable, f.name], capture_output=True, timeout=20, text=True)
                    ok = p.returncode == 0; err = p.stderr[-200:]
                except subprocess.TimeoutExpired:
                    err = "timeout"
                os.unlink(f.name)
            rows.append({"task": name, "pass": ok, "tokens": r.get("tokens"), "decode_tps": r.get("decode_tps"), "err": err})
        out[f"think={think}"] = {"passed": sum(x["pass"] for x in rows), "total": len(rows), "rows": rows}
    return out


def t_tools():
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "Clima atual de uma cidade",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    r = call([{"role": "user", "content": "Qual o clima em Sao Paulo agora? Use a ferramenta."}], tools=tools, think=False, max_tokens=300, extra={"tool_choice": "auto"})
    tc = r.get("tool_calls") or []
    args = {}
    try:
        args = json.loads(tc[0]["arguments"]) if tc else {}
    except Exception:
        pass
    return {"status": r["status"], "tool_calls": tc, "pass": bool(tc) and tc[0]["name"] == "get_weather" and "paulo" in json.dumps(args).lower()}


def t_concurrency():
    out = {}
    p = [{"role": "user", "content": "Escreva um script Python que le um CSV e imprime a media por coluna numerica."}]
    for n in (1, 2, 4, 8):
        t0 = time.time()
        with cf.ThreadPoolExecutor(n) as ex:
            rs = list(ex.map(lambda _: call(p, think=False, max_tokens=400), range(n)))
        wall = time.time() - t0
        ok = [r for r in rs if r["status"] == 200]
        toks = sum(r["tokens"] for r in ok)
        out[f"c={n}"] = {"ok": len(ok), "aggregate_tps": round(toks / wall, 1), "per_req_tps": summ([r["decode_tps"] for r in ok]),
                         "ttft": summ([r["ttft"] for r in ok]), "wall": round(wall, 1)}
    return out


def t_longgen():
    r = call([{"role": "user", "content": "Escreva um tutorial extenso e detalhado (pelo menos 3000 palavras) sobre design de APIs REST."}], think=False, max_tokens=4096)
    return {"status": r["status"], "tokens": r.get("tokens"), "decode_tps": r.get("decode_tps"), "ttft": r.get("ttft"), "finish": r.get("finish")}


def t_stability():
    """60 short requests, 4 parallel: error rate and latency tail."""
    p = [{"role": "user", "content": "Diga um fato curioso sobre astronomia em uma frase."}]
    with cf.ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: call(p, think=False, max_tokens=60), range(60)))
    ok = [r for r in rs if r["status"] == 200]
    t = sorted(r["total"] for r in ok)
    return {"requests": 60, "ok": len(ok), "errors": 60 - len(ok), "p50": round(t[len(t) // 2], 2) if t else None, "p95": round(t[int(len(t) * .95) - 1], 2) if t else None}


def t_prefix_cache():
    """Same long prompt twice: the second TTFT shows automatic prefix caching."""
    hay = filler(12000, 7)
    q = [{"role": "user", "content": hay + "\n\nResuma em uma frase quais palavras mais aparecem."}]
    a = call(q, think=False, max_tokens=30); b = call(q, think=False, max_tokens=30)
    return {"first_ttft": a.get("ttft"), "second_ttft": b.get("ttft"), "prompt_tokens": a.get("prompt_tokens"),
            "pass": bool(a.get("ttft") and b.get("ttft") and b["ttft"] < a["ttft"] * 0.6)}


def t_default_thinking():
    """What happens when the client says nothing about thinking (plain OpenAI client)."""
    r = call([{"role": "user", "content": "Quanto e 12*12?"}], think=None, max_tokens=300, extra={"chat_template_kwargs": {}})
    return {"reasoning_chars": len(r.get("reasoning") or ""), "content": (r.get("text") or "")[:60], "ttft": r.get("ttft")}


SECTIONS = {"auth": t_auth, "models": t_models, "speed": t_speed, "thinking": t_thinking, "coding": t_coding, "tools": t_tools,
            "context": t_context, "concurrency": t_concurrency, "longgen": t_longgen, "prefix_cache": t_prefix_cache, "default_thinking": t_default_thinking, "stability": t_stability}
import extra_sections  # noqa: E402  (vision, audio contract, parallel Q&A)
SECTIONS.update(extra_sections.make(call))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="reports/run.json"); ap.add_argument("--only", default="")
    a = ap.parse_args()
    if not KEY:
        sys.exit("set API_KEY")
    res = {"base_url": BASE, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sections": {}}
    for name, fn in SECTIONS.items():
        if a.only and name not in a.only.split(","):
            continue
        t0 = time.time()
        try:
            res["sections"][name] = fn()
        except Exception as e:
            res["sections"][name] = {"exception": repr(e)}
        print(f"[{name}] {time.time() - t0:.0f}s", json.dumps(res["sections"][name])[:300], flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1)
    print("saved", a.out)
