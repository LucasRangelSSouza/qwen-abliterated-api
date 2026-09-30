# CI and CD

This repository holds the code and its continuous integration. It holds no deployment secrets.

```text
                 public repository                                   infra repository (holds the secrets)
        LucasRangelSSouza/qwen-abliterated-api                          LucasRangelSSouza/vps_rt_infra
  ┌──────────────────────────────────────────┐                ┌──────────────────────────────────────────────┐
  │ validate.yml   (push, pull request)      │                │ deploy-qwen-vast.yml       (manual dispatch)  │
  │  terraform validate + fmt                │   checkout     │ deploy-qwen-terraform.yml  (manual dispatch)  │
  │  docker compose config                   │ ◄───────────── │  secrets: DEPLOY_SSH_PRIVATE_KEY, VLLM_API_KEY│
  │  bash -n scripts/*.sh                    │  at run time   │           HOSTINGER_API_KEY, EDGE_SSH_...     │
  │  python compile of the test scripts      │                │                                              │
  └──────────────────────────────────────────┘                └──────────────────────────────────────────────┘
```

## Why split it

A workflow that deploys needs SSH keys, a DNS token and the API key. Those belong in a repository whose visibility you control.
The code they deploy has no such need, so it can be public: readers can audit it, fork it, and CI on a public repository is free.
The deploy workflows therefore live in the infra repository and check the public repository out into `qwen/` on every run.

## What runs where

| Where | Workflow | Trigger | What it proves |
|---|---|---|---|
| this repository | `validate.yml` | push to `main`, pull requests | Terraform configurations are valid and formatted, the Compose file parses, every shell script has valid syntax, the Python tests compile |
| infra repository | `deploy-qwen-vast.yml` | manual | rebuilds the endpoint on any GPU container that ships `sshd`, then smoke-tests a completion |
| infra repository | `deploy-qwen-terraform.yml` | manual | the Docker and Caddy path for a plain Ubuntu GPU VM |

## Recreate the deploy side in your own infra repository

1. Create a private repository and add the secrets listed below.
2. Add a workflow that checks this repository out into a `qwen/` folder and runs the scripts from there:

```yaml
name: Deploy Qwen API to a Vast instance
on:
  workflow_dispatch:
    inputs:
      ssh_host:    { description: Vast public IP, required: true }
      ssh_port:    { description: Mapped SSH port, required: true }
      api_port:    { description: Mapped API port (container 8000), required: true }
      instance_id: { description: Vast instance id, required: true }
      public_host: { description: FQDN served by the edge, required: true }
      edge_ip:     { description: Edge host IPv4 that Traefik listens on, required: true }
jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: qwen } }
    steps:
      - uses: actions/checkout@v4
        with: { repository: LucasRangelSSouza/qwen-abliterated-api, path: qwen }
      # then the steps of the reference workflow: SSH keys, configure-vast-vllm.sh,
      # publish-endpoint.sh, dns-upsert.sh, smoke-test.sh
```

| Secret | Used for |
|---|---|
| `DEPLOY_SSH_PRIVATE_KEY` | root SSH to the GPU container (register the public key on the provider account) |
| `VLLM_API_KEY` | the stable bearer key clients use |
| `HOSTINGER_API_KEY` | DNS record for the public name (replace `scripts/dns-upsert.sh` for another DNS host) |
| `EDGE_SSH_PRIVATE_KEY` | SSH to the edge host that runs Traefik. Prefer a scoped deploy user over root |

Never put these secrets in a public repository. The scripts read them from the environment and never print them.
