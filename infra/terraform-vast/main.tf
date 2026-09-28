# Idempotent desired-state for the Qwen API on a Vast container.
# Each step re-runs only when its inputs change (triggers_replace); each script is itself idempotent, so
# `terraform apply` twice is a no-op and `terraform apply` after changing only the instance address rebuilds
# configuration, route and DNS without manual commands.

locals {
  scripts  = "${path.module}/../../scripts"
  ssh_opts = "-o BatchMode=yes -o StrictHostKeyChecking=accept-new -i ${var.ssh_private_key_path} -p ${var.vast_ssh_port}"
  ssh      = "ssh ${local.ssh_opts} root@${var.vast_ssh_host}"
}

# 1. weights + vLLM config on the instance
resource "terraform_data" "configure" {
  triggers_replace = {
    host    = "${var.vast_ssh_host}:${var.vast_ssh_port}"
    script  = filesha256("${local.scripts}/configure-vast-vllm.sh")
    profile = var.profile
    max_len = var.max_model_len
    prune   = var.prune_unused
    whisper = var.whisper_enabled
    key     = sha256(var.vllm_api_key)
  }
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      VLLM_API_KEY = var.vllm_api_key
    }
    command = <<-EOT
      set -euo pipefail
      tr -d '\r' < ${local.scripts}/configure-vast-vllm.sh | ${local.ssh} "cat > /root/configure.sh && chmod +x /root/configure.sh"
      ${local.ssh} "WHISPER=${var.whisper_enabled ? 1 : 0} PROFILE=${var.profile} MAX_LEN=${var.max_model_len} PRUNE_UNUSED=${var.prune_unused ? 1 : 0} VLLM_API_KEY='$VLLM_API_KEY' /root/configure.sh"
    EOT
  }
}

# 2. Traefik route with the Vast edge cookie injected (re-runs when IP/port/instance/script change)
resource "terraform_data" "publish" {
  depends_on = [terraform_data.configure]
  triggers_replace = {
    target  = "${var.vast_ssh_host}:${var.vast_api_port}"
    whisper = var.whisper_enabled ? var.vast_whisper_port : 0
    label   = var.vast_instance_id
    host    = var.public_host
    script  = filesha256("${local.scripts}/publish-endpoint.sh")
    configd = terraform_data.configure.id
  }
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      EDGE_SSH     = var.edge_ssh
      EDGE_KEY     = var.edge_ssh_key_path
      PUBLIC_HOST  = var.public_host
      VAST_IP      = var.vast_ssh_host
      VAST_PORT    = tostring(var.vast_api_port)
      VAST_LABEL   = "C.${var.vast_instance_id}"
      WHISPER_PORT = var.whisper_enabled ? tostring(var.vast_whisper_port) : ""
    }
    command = <<-EOT
      set -euo pipefail
      export VAST_TOKEN=$(${local.ssh} 'echo $OPEN_BUTTON_TOKEN' | tail -1)
      bash ${local.scripts}/publish-endpoint.sh
    EOT
  }
}

# 3. DNS A record on Hostinger (touches only this record)
resource "terraform_data" "dns" {
  triggers_replace = {
    host   = var.public_host
    ip     = var.edge_ip
    script = filesha256("${local.scripts}/dns-upsert.sh")
  }
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      HOSTINGER_API_KEY = var.hostinger_api_key
    }
    command = "bash ${local.scripts}/dns-upsert.sh ${join(".", slice(split(".", var.public_host), 1, length(split(".", var.public_host))))} ${split(".", var.public_host)[0]} ${var.edge_ip}"
  }
}

# 4. acceptance: the public endpoint answers a real completion with the stable key
resource "terraform_data" "verify" {
  depends_on = [terraform_data.publish, terraform_data.dns]
  triggers_replace = {
    publish = terraform_data.publish.id
    dns     = terraform_data.dns.id
  }
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      VLLM_API_KEY = var.vllm_api_key
    }
    command = <<-EOT
      set -euo pipefail
      for i in $(seq 1 90); do
        code=$(curl -s -o /dev/null -w '%%{http_code}' -H "Authorization: Bearer $VLLM_API_KEY" https://${var.public_host}/v1/models || true)
        [ "$code" = 200 ] && break; sleep 10
      done
      bash ${local.scripts}/smoke-test.sh https://${var.public_host} "$VLLM_API_KEY"
    EOT
  }
}

output "endpoint" {
  value = "https://${var.public_host}/v1"
}
