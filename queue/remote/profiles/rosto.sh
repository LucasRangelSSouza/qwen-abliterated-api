# perfil ROSTO: troca de personagem (Openjutsu / MiniMax H3 + SAM 3.1), ~55 GB. Revisoes fixadas = as que rodam em producao (06/11/2026).
# LICENCA do MiniMax H3 (Community License): uso comercial autorizado fora de EUA, UE, Reino Unido e Coreia do Sul; exibir "MiniMax H3" no produto; termos de uso
# repassando as restricoes (sem imitar alguem sem consentimento); rotulo de conteudo sintetico. Ver docs/OPERACAO.md.
apt_base
comfy_install 1332fabfcc507aa7c548cd5152071ed494c207e8
no https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop.git 43afeffcc570beb5f7382593e79e4b211c7e02f6
no https://github.com/Adudeguyman/ComfyUI-Fantastic-MiniMaxH3-PromptBuilder.git 22e719fe89336c628383363182a4da3cb58d4f9b
no https://github.com/D3voz/comfyui-h3-oneclick-swap.git e44a56e92920000e675d14be484aea0d7498d63e
no https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git 4d907bee61e92c2e65af3bd6383a4e4d356126d1
no https://github.com/kijai/ComfyUI-KJNodes.git ab72eda7ffad6280422d71ab7d54d3fb87c0ba18
pip install -q imageio-ffmpeg scipy 2>/dev/null || true
M=/workspace/ComfyUI/models
H=https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main
baixar $H/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors $M/diffusion_models minimax_h3_ref2va_pruned_int8_convrot.safetensors &
baixar $H/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors $M/loras minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors &
baixar $H/vae/minimax_h3_video_vae_int8_convrot.safetensors $M/vae minimax_h3_video_vae_int8_convrot.safetensors &
baixar $H/vae/minimax_h3_audio_vae_fp32.safetensors $M/vae minimax_h3_audio_vae_fp32.safetensors &
baixar https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors $M/checkpoints sam3.1_multiplex_fp16.safetensors &
# encoders: os arquivos exatos que funcionam em producao, guardados num repositorio PRIVADO nosso (a origem comunitaria era incerta)
P=https://huggingface.co/Rangel-tech-ia/h3-swap-assets/resolve/main/text_encoders
baixar_priv $P/qwen3vl_32b_minimax_h3_int4_convrot.safetensors $M/text_encoders qwen3vl_32b_minimax_h3_int4_convrot.safetensors &
baixar_priv $P/qwen3vl_8b_int8_convrot.safetensors $M/text_encoders qwen3vl_8b_int8_convrot.safetensors &
wait
confere $M/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors 20000000000
confere $M/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors 1500000000
confere $M/vae/minimax_h3_video_vae_int8_convrot.safetensors 2000000000
confere $M/vae/minimax_h3_audio_vae_fp32.safetensors 400000000
confere $M/checkpoints/sam3.1_multiplex_fp16.safetensors 1500000000
confere $M/text_encoders/qwen3vl_32b_minimax_h3_int4_convrot.safetensors 14000000000
confere $M/text_encoders/qwen3vl_8b_int8_convrot.safetensors 9000000000
marca "modelos prontos"
comfy_start
pronto rosto
