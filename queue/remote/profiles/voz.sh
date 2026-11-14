# perfil VOZ: transcricao (faster-whisper, MIT), clonagem de voz (Qwen3-TTS 1.7B, Apache-2.0, 10 idiomas com portugues), conversao de voz (Chatterbox, MIT),
# separacao de fundo (demucs, MIT) e medida de semelhanca de voz (speechbrain, so QA). F5-TTS NAO entra: os pesos sao CC-BY-NC (nao comercial).
# Seed-VC (GPL-3.0, codigo usado como ferramenta separada, sem modificar) e o conversor principal desde 14/11/2026: medido com o encoder de falante
# do XTTS (teto mesma pessoa 0,86), Seed-VC 0,75 contra Chatterbox 0,59 e OpenVoice 0,48. Chatterbox fica de reserva.
# Tres ambientes porque Qwen3-TTS, Chatterbox e Seed-VC pedem versoes diferentes do PyTorch/transformers. Os scripts (clone.py, dub_a.py...) chegam junto com este instalador.
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
SVC_COMMIT=51383efd921027683c89e5348211d93ff12ac2a8
if [ ! -d $V/seed-vc/.git ]; then git clone -q https://github.com/Plachtaa/seed-vc.git $V/seed-vc; fi
git -C $V/seed-vc fetch -q origin $SVC_COMMIT 2>/dev/null; git -C $V/seed-vc checkout -q $SVC_COMMIT
[ -x $V/venvs/svc/bin/python ] || uv venv -q --python 3.10 $V/venvs/svc
uv pip install -q --python $V/venvs/svc/bin/python torch==2.6.0 torchaudio==2.6.0 scipy==1.13.1 librosa==0.10.2 'huggingface-hub>=0.28.1' munch==4.0.0 einops==0.8.0 descript-audio-codec==1.0.0 transformers==4.46.3 soundfile pydub numpy==1.26.4 hydra-core==1.3.2 pyyaml python-dotenv accelerate
marca "venv svc ok"
# modelos ja na maquina (descobrir erro no meio do pedido custa mais)
$V/venvs/main/bin/python $V/baixar_modelos.py main
marca "modelos main ok"
$V/venvs/cb/bin/python $V/baixar_modelos.py cb
marca "modelos cb ok"
(cd $V && $V/venvs/svc/bin/python $V/baixar_modelos.py svc)
marca "modelos svc ok"
pronto voz
