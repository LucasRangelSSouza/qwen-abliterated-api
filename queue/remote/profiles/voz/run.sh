#!/bin/bash
# Roda um script Python da pilha de voz no ambiente certo, com as bibliotecas CUDA do pip no caminho (o ctranslate2 do faster-whisper precisa de cuDNN 9 e cuBLAS 12).
# uso: bash run.sh main|cb script.py args...
V=/workspace/voz
ENV=${1:-main}; shift
PY=$V/venvs/$ENV/bin/python
L=$($PY -c "import glob,os,site;print(':'.join(d for sp in site.getsitepackages() for d in glob.glob(os.path.join(sp,'nvidia','*','lib'))))" 2>/dev/null)
export LD_LIBRARY_PATH="$L:$LD_LIBRARY_PATH"
export PYTHONUNBUFFERED=1
cd $V
exec $PY "$@"
