# Runbook

## Facts

| item | value |
|---|---|
| public URL | `https://qwen.rangeltech.net/v1` |
| model name | `qwen-abliterated` |
| instance | Vast `53155573`, GB10, Texas, ~US$ 0.45/h while running |
| SSH | `ssh -i <deploy key> -p <VAST_TCP_PORT_22> root@<PUBLIC_IPADDR>` (currently 41083) |
| edge | VPS `66.94.101.153` (Traefik), DNS at Hostinger, zone `rangeltech.net` |
| keys | vault `personal-skills/secrets/qwen-api.env`; GitHub secrets on this repo |

Ports and IP are per-instance. Read them from inside the container:

```bash
env | grep -E 'PUBLIC_IPADDR|VAST_TCP_PORT_(22|8000)|CONTAINER_ID|OPEN_BUTTON_TOKEN'
```

## Turn it on and off programmatically

The account API key is required to **start** (a stopped container cannot authorise itself). Create it once at console.vast.ai → Account → API Keys and store it as `VAST_API_KEY` (vault + GitHub secret).

```bash
export VAST_API_KEY=... VAST_INSTANCE_ID=53155573
scripts/vast-power.sh status
scripts/vast-power.sh stop      # GPU billing stops; disk (~US$0.007/h) keeps weights
scripts/vast-power.sh start     # weights are still on disk; vLLM comes back by itself (~8 min measured, same IP and ports)
```

Stopping from inside the instance works with the container-scoped key Vast injects (`CONTAINER_API_KEY`), useful for idle auto-shutdown:

```bash
vastai stop instance $CONTAINER_ID --api-key $CONTAINER_API_KEY
```

That key is rejected from outside the container ("Unable to authorize instance API key") and cannot start anything.

`destroy` is different: it deletes the disk and the weights. Never use it as "off".

After a **start**, check whether IP/ports changed; if they did, re-publish:

```bash
scripts/publish-endpoint.sh      # env from vault: EDGE_*, VAST_IP, VAST_PORT, VAST_LABEL, VAST_TOKEN
```

## Rebuild on a new instance

Actions → **Deploy to Vast instance** with the four instance inputs. Locally, the same steps:

```bash
scp/ssh scripts/configure-vast-vllm.sh → /root/configure.sh
VLLM_API_KEY=... /root/configure.sh                # downloads weights once, writes config, starts vLLM
scripts/publish-endpoint.sh && scripts/dns-upsert.sh rangeltech.net qwen 66.94.101.153
scripts/smoke-test.sh https://qwen.rangeltech.net "$VLLM_API_KEY"
```

## Health and logs

```bash
supervisorctl status vllm
tail -f /var/log/vllm.log            # look for "Application startup complete" and "SpecDecoding metrics"
curl -s localhost:18000/health
nvidia-smi
```

## Known failure modes

| symptom | cause | fix |
|---|---|---|
| 502 from the public URL | vLLM still loading (up to ~8 min for FP8) or stopped | wait (FP8: up to ~8 min) / `supervisorctl start vllm` |
| `Address already in use` in vllm.log | old engine still holds :18000 | `pkill -f "[v]llm serve"; pkill -f "[V]LLM::EngineCor"` then start |
| 401 from the public URL | wrong `VLLM_API_KEY` | key is in the vault |
| 401 only after an instance recreate | Traefik still injects the previous Vast token | re-run `publish-endpoint.sh` |
| 4 tok/s | wrong checkpoint (BF16) or drafter missing | check `VLLM_ARGS` in `/workspace/.env` |
| TLS not issued for a new hostname | no DNS A record yet | `dns-upsert.sh`, then `docker restart traefik` on the edge |

## Cost discipline

Running ≈ US$ 0.45/h; stopped ≈ US$ 0.007/h. Stop the instance whenever nothing needs it. Never leave test instances alive: listing them is `vastai show instances`.
