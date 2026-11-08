# perfil VIDEO: Wan 2.2 A14B imagem->video em fp16 (2 x 28,6 GB) + encoder + VAE + Flux (primeiro quadro). Medido: ~145 s numa rede de 18 Gbps.
apt_base
comfy_install
M=/workspace/ComfyUI/models
HF=https://huggingface.co/Comfy-Org/Wan-2.2/resolve/main/split_files
baixar $HF/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors $M/diffusion_models wan2.2_i2v_high_noise_14B_fp16.safetensors &
baixar $HF/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors $M/diffusion_models wan2.2_i2v_low_noise_14B_fp16.safetensors &
baixar $HF/text_encoders/umt5_xxl_fp16.safetensors $M/text_encoders umt5_xxl_fp16.safetensors &
baixar $HF/vae/wan_2.1_vae.safetensors $M/vae wan_2.1_vae.safetensors &
baixar https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors $M/checkpoints flux1-schnell-fp8.safetensors &
wait
confere $M/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors 28000000000
confere $M/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors 28000000000
confere $M/text_encoders/umt5_xxl_fp16.safetensors 11000000000
confere $M/vae/wan_2.1_vae.safetensors 200000000
confere $M/checkpoints/flux1-schnell-fp8.safetensors 16000000000
marca "modelos prontos"
comfy_start
pronto video
