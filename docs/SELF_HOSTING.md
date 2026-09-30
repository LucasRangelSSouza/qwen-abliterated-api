# Self-hosting guide: from an empty GPU to a validated endpoint

This is the path a stranger follows to reproduce the setup. Each step ends with a check, and you do not move on until the check passes.

## 0. What you need

| Item | Notes |
|---|---|
| A rented GPU container with `sshd` | tuned on an NVIDIA GB10 (119 GB unified memory) on Vast.ai; any GPU that holds a 27B model in FP8 works with a smaller context |
| An edge host running Traefik | gives the endpoint a stable HTTPS name, because the provider changes ports and addresses on rebuild |
| DNS API access | the scripts use Hostinger; replace `scripts/dns-upsert.sh` for another host |
| A local machine with `bash`, `ssh`, `python3` (3.10+), `terraform` (1.5+) | nothing else |

Register your deploy public key on the provider **account**, not only on the instance. The provider rewrites `authorized_keys` from account keys.

## 1. Clone and check the repository

```bash
git clone https://github.com/LucasRangelSSouza/qwen-abliterated-api
cd qwen-abliterated-api
bash -n scripts/*.sh && echo "shell syntax ok"
python3 -m py_compile tests/*.py && echo "python ok"
terraform -chdir=infra/terraform-vast init -backend=false && terraform -chdir=infra/terraform-vast validate
```

Check: the three commands print `ok` and `Success! The configuration is valid.`

## 2. Configure the variables

```bash
cd infra/terraform-vast
cp terraform.tfvars.example terraform.tfvars    # git-ignored
$EDITOR terraform.tfvars                        # instance address, mapped ports, keys, edge host, public name
```

The mapped ports come from the provider: the container's port 22 (SSH), 8000 (API) and 3000 (speech-to-text) each get a random high host port.

## 3. Bring it up

```bash
terraform apply       # configure vLLM, publish the route, upsert DNS, run the acceptance test
```

The first run downloads about 56 GB of weights (roughly 10 minutes) and quantises to FP8 at load. Later runs move no bytes.

Check: `terraform plan -detailed-exitcode` exits with code 0, which means nothing is left to change.

## 4. Validate the endpoint

```bash
export BASE_URL=https://qwen.example.com/v1 API_KEY=<your VLLM_API_KEY>
curl -s -o /dev/null -w "%{http_code}\n" "$BASE_URL/models"                                   # 401, no key
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $API_KEY" "$BASE_URL/models"   # 200
scripts/smoke-test.sh "${BASE_URL%/v1}" "$API_KEY"
```

Check: 401 then 200, and the smoke test prints a completion.

## 5. Measure it

```bash
python3 tests/suite.py --out reports/runs/mine.json        # about 25 minutes: speed, context, tools, vision, stability
python3 tests/quality.py fp8                                # HumanEval and GSM8K, about 17 minutes
python3 tests/longctx.py                                    # needle in a haystack up to 150k tokens
python3 tests/refusal.py openai mine                        # refusal probe, about 5 minutes
python3 tests/make_results.py > docs/RESULTS.md
```

Check: your decode speed on code should sit near 33 to 36 tokens per second on a GB10. On a different GPU, divide your memory bandwidth by 27 GB (FP8 weights) to get the plain-decoding ceiling and expect roughly three times that with speculative decoding on code.

## 6. Prove it is idempotent

```bash
python3 tests/idempotency.py      # needs the SSH and provider variables in its docstring
```

Check: 16 of 16 steps pass, including a real stop and start through the provider API.

## 7. Turn it off when you are not using it

```bash
scripts/vast-power.sh stop
```

A stopped instance costs about US$ 0.007 per hour for disk. Destroying the instance deletes the weights, so never use destroy as "off".

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| SSH works from the provider console but not from your key | key registered on the instance only | register it on the account and restart the instance |
| API answers 401 with the right key | the edge did not inject the provider token | re-run `scripts/publish-endpoint.sh` |
| Long prompts come back as gibberish | fp8 KV cache | keep `KV_DTYPE=auto`, then run `tests/longctx.py` |
| Everything is slow (4 to 5 tokens per second) | BF16 weights, no drafter | use the default FP8 profile with the DFlash2 drafter |
