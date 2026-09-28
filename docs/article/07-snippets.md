# Snippets worth quoting

Short, self-contained excerpts. Each one is copied from the repository (the path is given) and trimmed for the page; re-check them against the source before publishing, and replace hostnames with placeholders.

## 1. The ceiling, as code

```python
def decode_ceiling_tok_s(bandwidth_gb_s: float, weight_gb: float) -> float:
    """Single-stream decode reads every weight once per token."""
    return bandwidth_gb_s / weight_gb

decode_ceiling_tok_s(273, 54)   # ~5.1  (BF16 27B on a 273 GB/s GPU)
decode_ceiling_tok_s(273, 27)   # ~10.1 (FP8)
```

Measured: 4.4 tok/s for BF16. The point of the snippet is that the check takes one line and can be run before renting anything.

## 2. Idempotent configuration: compare, then act (`scripts/configure-vast-vllm.sh`)

```bash
CURRENT=$(sed -n '/^# BEGIN qwen-abliterated-api/,/^# END qwen-abliterated-api/p' "$ENV_FILE")
if [ "$CURRENT" = "$DESIRED" ] && supervisorctl status vllm | grep -q RUNNING; then
  # Right config and the process is up: it may simply still be loading weights (minutes).
  for _ in $(seq 1 ${HEALTH_WAIT_S:-900}); do
    if healthy; then echo "nothing to do"; exit 0; fi
    sleep 1
  done
fi
```

The bug this fixed: the first version treated "not healthy yet" as "broken" and restarted a server that was still loading. The idempotency test found it.

## 3. Killing a process tree without killing your own shell

```bash
supervisorctl stop vllm            # leaves the API server and engine children holding the port
pkill -f "[v]llm serve"            # the bracket keeps the pattern from matching this very command line
pkill -f "[V]LLM::EngineCor"
```

## 4. One key for clients, a rotating token for the provider (`scripts/publish-endpoint.sh`, redacted)

```yaml
http:
  middlewares:
    qwen-vast-auth:
      headers:
        customRequestHeaders:
          Cookie: "C.<instance-id>_auth_token=<provider-token>"   # never leaves the edge
  routers:
    qwen-vast-router:
      rule: "Host(`api.example.com`)"
      service: qwen-vast
      middlewares: [qwen-vast-auth]
  services:
    qwen-vast:
      loadBalancer:
        responseForwarding: { flushInterval: 1ms }   # keeps token streaming smooth
        servers: [{ url: "http://<gpu-host>:<mapped-port>" }]
```

The provider's proxy accepts bearer, query or cookie. The cookie leaves `Authorization` free for the model server's own `--api-key`.

## 5. The test that would have caught the KV-cache bug (`tests/longctx.py`, simplified)

```python
needle = f"CODIGO-SECRETO-{40000 + size % 997 + seed}"
haystack = filler(size, seed)
mid = len(haystack) // 2
prompt = haystack[:mid] + f"\n\nA senha do cofre e {needle}.\n\n" + haystack[mid:] \
         + "\n\nQual e a senha do cofre? Responda so com a senha."
ok = needle in ask(prompt, max_tokens=40)
```

Run it at sizes you actually intend to serve, with several seeds, and read the failures: the wrong answers were not "no", they were garbage.

## 6. Paired significance in twelve lines (`tests/make_results.py`)

```python
def mcnemar(a, b):                     # a, b: per-problem pass/fail lists, same problems
    x = sum(1 for p, r in zip(a, b) if p and not r)   # A right, B wrong
    y = sum(1 for p, r in zip(a, b) if r and not p)
    k = x + y
    p = min(1.0, 2 * sum(math.comb(k, i) for i in range(min(x, y) + 1)) / 2 ** k) if k else 1.0
    return x, y, p
```

Only the problems where the two models disagree carry information; that is why 164 problems can show p = 0.002 for a 6-point gap and p = 0.125 for a 2.4-point one.

## 7. The yardstick call (`tests/quality_claude.py`)

```bash
echo "$PROMPT" | claude -p --model claude-sonnet-5 --effort medium --tools "" \
    --no-session-persistence --disable-slash-commands --setting-sources "" --output-format json
```

`--setting-sources ""` cut the per-call overhead from ~43 000 to ~3 700 tokens by not loading project settings and memory.

## 8. The whole rebuild, three commands

```bash
cd infra/terraform-vast && terraform init -backend=false
terraform apply          # configure → route → DNS → acceptance test
terraform plan -detailed-exitcode   # 0 = nothing to change
```
