#!/bin/bash
# Biblioteca comum dos instaladores de perfil do worker. O worker envia esta biblioteca + o script do perfil e roda na maquina recem-alugada.
# Cada perfil escreve /workspace/profile-<nome>.done ao terminar e deixa o tempo de cada etapa em /workspace/provision.log.
set -e
export DEBIAN_FRONTEND=noninteractive
mkdir -p /workspace && cd /workspace
T0=$(date +%s)
marca() { echo "[$(( $(date +%s) - T0 ))s] $*" >> /workspace/provision.log; }

apt_base() {
  local pk="git ffmpeg libgl1 libglib2.0-0 aria2 sox libsndfile1"
  (apt-get install -y -qq $pk >/dev/null 2>&1) || (apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq $pk >/dev/null 2>&1) || true
  marca "apt ok"
}

# comfy_install [commit]: ComfyUI (opcionalmente numa revisao fixa) + dependencias
comfy_install() {
  [ -d /workspace/ComfyUI ] || git clone -q https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
  cd /workspace/ComfyUI
  if [ -n "$1" ] && [ "$(git rev-parse HEAD)" != "$1" ]; then
    git fetch -q origin "$1" 2>/dev/null || git fetch -q --unshallow origin 2>/dev/null || true
    git checkout -q "$1" || true
  fi
  pip install -q -r requirements.txt
  cd /workspace
  marca "comfyui ok ($(git -C /workspace/ComfyUI rev-parse --short HEAD))"
}

# baixar url pasta arquivo: download paralelo e retomavel. baixar_priv usa o HF_TOKEN (so para repositorio privado nosso).
baixar() {
  mkdir -p "$2"
  if [ ! -f "$2/$3" ] || [ -f "$2/$3.aria2" ]; then
    aria2c -x16 -s16 -k4M -c -q --console-log-level=warn --max-tries=5 --retry-wait=3 -d "$2" -o "$3" "$1"
  fi
  marca "baixado $3 ($(du -h "$2/$3" | cut -f1))"
}
baixar_priv() {
  mkdir -p "$2"
  if [ ! -f "$2/$3" ] || [ -f "$2/$3.aria2" ]; then
    aria2c -x16 -s16 -k4M -c -q --console-log-level=warn --max-tries=5 --retry-wait=3 --header="Authorization: Bearer $HF_TOKEN" -d "$2" -o "$3" "$1"
  fi
  marca "baixado $3 ($(du -h "$2/$3" | cut -f1))"
}
# confere arquivo tamanho-minimo: os downloads rodam em paralelo e um erro em segundo plano nao derruba o script
confere() {
  { [ -f "$1" ] && [ ! -f "$1.aria2" ] && [ "$(stat -c %s "$1")" -gt "$2" ]; } || { marca "ARQUIVO INCOMPLETO $1"; exit 3; }
}
# no url commit: nos customizados do ComfyUI numa revisao fixa (a que funciona em producao)
no() {
  local d=/workspace/ComfyUI/custom_nodes/$(basename "$1" .git)
  [ -d "$d" ] || git clone -q "$1" "$d"
  git -C "$d" checkout -q "$2" || marca "NO sem a revisao $2 ($1): segue na atual"
  if [ -f "$d/requirements.txt" ]; then pip install -q -r "$d/requirements.txt" || true; fi
  marca "no $(basename "$d")"
}
comfy_start() {
  pkill -f "python main.py --listen" 2>/dev/null || true
  sleep 2
  cd /workspace/ComfyUI
  nohup setsid python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-metadata >> /workspace/comfy.log 2>&1 < /dev/null &
  cd /workspace
  for _ in $(seq 1 100); do
    if curl -sf -m 3 http://127.0.0.1:8188/system_stats >/dev/null; then marca "ComfyUI respondendo"; return 0; fi
    sleep 3
  done
  marca "ComfyUI NAO subiu"
  exit 4
}
pronto() { echo OK > "/workspace/profile-$1.done"; marca "perfil $1 PRONTO"; }
