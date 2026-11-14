"""Baixa os modelos da pilha de voz durante a instalacao do perfil (descobrir erro no meio de um pedido custa mais). uso: python baixar_modelos.py main|cb|svc (rodar em /workspace/voz)"""
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
elif sys.argv[1] == "svc":
    sys.path.insert(0, "/workspace/voz/seed-vc")  # modelos em /workspace/voz/checkpoints, a mesma pasta de trabalho do run.sh
    from hf_utils import load_custom_model_from_hf
    load_custom_model_from_hf("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth", "config_dit_mel_seed_uvit_whisper_small_wavenet.yml")
    load_custom_model_from_hf("funasr/campplus", "campplus_cn_common.bin", config_filename=None)
    snapshot_download("nvidia/bigvgan_v2_22khz_80band_256x")
    snapshot_download("openai/whisper-small")
else:
    snapshot_download("ResembleAI/chatterbox")
print("modelos ok", sys.argv[1])
