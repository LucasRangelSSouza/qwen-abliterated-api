"""Baixa os modelos da pilha de voz durante a instalacao do perfil (descobrir erro no meio de um pedido custa mais). uso: python baixar_modelos.py main|cb"""
import sys

from huggingface_hub import snapshot_download

if sys.argv[1] == "main":
    snapshot_download("Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    from faster_whisper import WhisperModel
    WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    from demucs.pretrained import get_model
    get_model("htdemucs")
    try:
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except Exception:  # noqa: BLE001
            from speechbrain.pretrained import EncoderClassifier
        EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir="/workspace/voz/ecapa", run_opts={"device": "cpu"})
    except Exception as e:  # noqa: BLE001
        print("aviso: medida de semelhanca indisponivel:", e)
else:
    snapshot_download("ResembleAI/chatterbox")
print("modelos ok", sys.argv[1])
