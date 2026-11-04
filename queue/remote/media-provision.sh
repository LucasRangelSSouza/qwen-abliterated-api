#!/bin/bash
# Prepara a GPU de mídia: ComfyUI + modelo de imagem. Idempotente (pode rodar de novo).
set -e
export DEBIAN_FRONTEND=noninteractive
(apt-get install -y -qq git ffmpeg libgl1 libglib2.0-0 aria2 >/dev/null 2>&1) || (apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq git ffmpeg libgl1 libglib2.0-0 aria2 >/dev/null 2>&1)
mkdir -p /workspace && cd /workspace
[ -d ComfyUI ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git >/dev/null 2>&1
cd ComfyUI
git log -1 --format="comfyui %H %cd" > /workspace/comfyui.versao
pip install -q -r requirements.txt
mkdir -p models/checkpoints
F=models/checkpoints/flux1-schnell-fp8.safetensors
if [ ! -f "$F" ] || [ -f "$F.aria2" ]; then
  aria2c -x8 -s8 -c -q --console-log-level=warn -d models/checkpoints -o flux1-schnell-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors
fi
python -c "import torch;print('torch',torch.__version__,torch.cuda.is_available())"
echo OK > /workspace/provision.done
