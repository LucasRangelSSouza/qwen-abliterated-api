locals {
  repository_root = abspath("${path.module}/../..")
  deploy_files = concat(
    ["compose.yaml", "Caddyfile"],
    [for file in fileset("${local.repository_root}/scripts", "**") : "scripts/${file}"],
  )
  deploy_env = join("\n", [
    "API_DOMAIN=${var.api_domain}",
    "VLLM_API_KEY=${var.vllm_api_key}",
    "MODEL_ID=${var.model_id}",
    "SERVED_MODEL_NAME=${var.served_model_name}",
    "VLLM_IMAGE=${var.vllm_image}",
    "HF_HOME=/opt/qwen-abliterated-api/model-cache",
    "GPU_MEMORY_UTILIZATION=0.90",
    "MAX_MODEL_LEN=32768",
    "MAX_NUM_SEQS=8",
    "ENABLE_REASONING=true",
    "ENABLE_TOOL_CALLING=true",
    "",
  ])
}

resource "terraform_data" "vm_deploy" {
  triggers_replace = {
    host          = var.ssh_host
    user          = var.ssh_user
    source_digest = sha256(join("", [for file in local.deploy_files : filesha256("${local.repository_root}/${file}")]))
    config_digest = sha256(local.deploy_env)
  }

  connection {
    type        = "ssh"
    host        = var.ssh_host
    user        = var.ssh_user
    private_key = file(var.ssh_private_key_path)
  }

  provisioner "remote-exec" {
    inline = ["rm -rf /tmp/qwen-abliterated-api && mkdir -p /tmp/qwen-abliterated-api"]
  }

  provisioner "file" {
    source      = "${local.repository_root}/compose.yaml"
    destination = "/tmp/qwen-abliterated-api/compose.yaml"
  }

  provisioner "file" {
    source      = "${local.repository_root}/Caddyfile"
    destination = "/tmp/qwen-abliterated-api/Caddyfile"
  }

  provisioner "file" {
    source      = "${local.repository_root}/scripts"
    destination = "/tmp/qwen-abliterated-api/scripts"
  }

  provisioner "file" {
    content     = local.deploy_env
    destination = "/tmp/qwen-abliterated-api/.env"
  }

  provisioner "remote-exec" {
    inline = [
      "sudo -n bash /tmp/qwen-abliterated-api/scripts/bootstrap-vm.sh",
      "sudo -n rm -rf /opt/qwen-abliterated-api",
      "sudo -n mv /tmp/qwen-abliterated-api /opt/qwen-abliterated-api",
      "sudo -n chown -R root:root /opt/qwen-abliterated-api",
      "sudo -n chmod 600 /opt/qwen-abliterated-api/.env",
      "sudo -n bash /opt/qwen-abliterated-api/scripts/deploy-vm.sh",
    ]
  }
}
