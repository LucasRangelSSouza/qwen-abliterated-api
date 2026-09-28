#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/qwen-abliterated-api}"
cd "$ROOT_DIR"

test -f .env || { echo "Missing $ROOT_DIR/.env" >&2; exit 2; }
command -v docker >/dev/null || { echo "Docker is required on a VM deployment" >&2; exit 3; }
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml pull
docker compose --env-file .env -f compose.yaml up -d --remove-orphans
docker compose --env-file .env -f compose.yaml ps

