#!/bin/bash
# Prepara o worker de video (RTX PRO 6000 alugada sob demanda): ComfyUI + Wan 2.2 A14B (imagem para video, fp16) + Flux schnell (imagem inicial).
# Roda numa maquina NOVA a cada partida (o worker e destruido quando fica ocioso). Medido em 08/11/2026: ~145 s com rede de 18 Gbps.
# Escreve /workspace/provision.log (tempo de cada etapa) e, ao final, /workspace/provision.done com o ComfyUI ja respondendo em 127.0.0.1:8188.
set -e
export DEBIAN_FRONTEND=noninteractive
T0=$(date +%s)
marca() { echo "[$(( $(date +%s) - T0 ))s] $*" >> /workspace/provision.log; }
mkdir -p /workspace && cd /workspace
rm -f provision.done
(apt-get install -y -qq git ffmpeg libgl1 libglib2.0-0 aria2 >/dev/null 2>&1) || (apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq git ffmpeg libgl1 libglib2.0-0 aria2 >/dev/null 2>&1)
marca "apt ok"
[ -d ComfyUI ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git >/dev/null 2>&1
cd ComfyUI
pip install -q -r requirements.txt
marca "comfyui ok"

HF=https://huggingface.co/Comfy-Org/Wan-2.2/resolve/main/split_files
baixar() { # url pasta arquivo (retoma download interrompido; confere o tamanho minimo)
  mkdir -p "$2"
  if [ ! -f "$2/$3" ] || [ -f "$2/$3.aria2" ]; then
    aria2c -x16 -s16 -k4M -c -q --console-log-level=warn --max-tries=5 --retry-wait=3 -d "$2" -o "$3" "$1"
  fi
  marca "baixado $3 ($(du -h "$2/$3" | cut -f1))"
}
baixar $HF/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors models/diffusion_models wan2.2_i2v_high_noise_14B_fp16.safetensors &
baixar $HF/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors models/diffusion_models wan2.2_i2v_low_noise_14B_fp16.safetensors &
baixar $HF/text_encoders/umt5_xxl_fp16.safetensors models/text_encoders umt5_xxl_fp16.safetensors &
baixar $HF/vae/wan_2.1_vae.safetensors models/vae wan_2.1_vae.safetensors &
baixar https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors models/checkpoints flux1-schnell-fp8.safetensors &
wait
# confere que nenhum arquivo ficou pela metade (os downloads rodam em paralelo e um erro em segundo plano nao derruba o script)
confere() { # arquivo tamanho-minimo-em-bytes
  { [ -f "$1" ] && [ ! -f "$1.aria2" ] && [ "$(stat -c %s "$1")" -gt "$2" ]; } || { marca "ARQUIVO INCOMPLETO $1"; exit 3; }
}
confere models/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors 28000000000
confere models/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors 28000000000
confere models/text_encoders/umt5_xxl_fp16.safetensors 11000000000
confere models/vae/wan_2.1_vae.safetensors 200000000
confere models/checkpoints/flux1-schnell-fp8.safetensors 16000000000
marca "modelos prontos"

pkill -f "python main.py --listen" 2>/dev/null || true
nohup setsid python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-metadata >> /workspace/comfy.log 2>&1 < /dev/null &
for _ in $(seq 1 60); do
  if curl -sf -m 3 http://127.0.0.1:8188/system_stats >/dev/null; then
    marca "ComfyUI respondendo"
    echo OK > /workspace/provision.done
    exit 0
  fi
  sleep 3
done
marca "ComfyUI NAO subiu"
exit 4
