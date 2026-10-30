#!/bin/bash
# Gate do release: testes + a mesma imagem sobe e responde /health. Sem GPU e sem upstream.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-verify >/dev/null 2>&1 || true
# shellcheck disable=SC1091
. .venv-verify/bin/activate 2>/dev/null || . .venv-verify/Scripts/activate
pip install -q -r requirements-dev.txt
python -m pytest -q
if command -v docker >/dev/null 2>&1; then
  docker build -q -t llm-access:verify .
  cid=$(docker run -d --rm -e ADMIN_PASSWORD=verify-only -p 18089:8000 llm-access:verify)
  trap 'docker stop "$cid" >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:18089/health >/dev/null 2>&1; then echo "imagem saudavel"; exit 0; fi
    sleep 1
  done
  docker logs "$cid" | tail -30
  echo "FALHA: imagem nao respondeu /health" >&2
  exit 1
fi
echo "docker ausente: so os testes rodaram"
