# perfil VOZ: transcricao (faster-whisper, MIT), clonagem de voz (Qwen3-TTS 1.7B, Apache-2.0, 10 idiomas com portugues), conversao de voz (Chatterbox, MIT),
# separacao de fundo (demucs, MIT) e medida de semelhanca de voz (speechbrain, so QA). F5-TTS NAO entra: os pesos sao CC-BY-NC (nao comercial).
# Dois ambientes porque Qwen3-TTS e Chatterbox pedem versoes diferentes do PyTorch. Os scripts (clone.py, dub_a.py...) chegam junto com este instalador.
apt_base
pip install -q uv
V=/workspace/voz
mkdir -p $V/work
[ -x $V/venvs/main/bin/python ] || uv venv -q --python 3.12 $V/venvs/main
uv pip install -q --python $V/venvs/main/bin/python qwen-tts faster-whisper demucs soundfile speechbrain huggingface_hub 'setuptools<81'
marca "venv main ok"
[ -x $V/venvs/cb/bin/python ] || uv venv -q --python 3.11 $V/venvs/cb
uv pip install -q --python $V/venvs/cb/bin/python chatterbox-tts 'setuptools<81'
marca "venv cb ok"
# modelos ja na maquina (descobrir erro no meio do pedido custa mais)
$V/venvs/main/bin/python $V/baixar_modelos.py main
marca "modelos main ok"
$V/venvs/cb/bin/python $V/baixar_modelos.py cb
marca "modelos cb ok"
pronto voz
