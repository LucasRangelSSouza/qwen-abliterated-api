variable "vast_ssh_host" {
  description = "Vast public IP of the instance (PUBLIC_IPADDR)"
  type        = string
}
variable "vast_ssh_port" {
  description = "Vast mapped port for container port 22 (VAST_TCP_PORT_22)"
  type        = number
}
variable "vast_api_port" {
  description = "Vast mapped port for container port 8000 (VAST_TCP_PORT_8000), the Caddy edge in front of vLLM"
  type        = number
}
variable "vast_instance_id" {
  description = "Vast instance id (CONTAINER_ID); the Caddy cookie is named C.<id>_auth_token"
  type        = string
}
variable "ssh_private_key_path" {
  description = "Private key whose public half is registered on the Vast account/instance"
  type        = string
}
variable "vllm_api_key" {
  description = "Stable client bearer key enforced by vLLM"
  type        = string
  sensitive   = true
}
variable "hostinger_api_key" {
  description = "Hostinger DNS API key (zone edit)"
  type        = string
  sensitive   = true
}
variable "edge_ssh" {
  description = "user@host of the edge VPS running Traefik"
  type        = string
  default     = "root@66.94.101.153"
}
variable "edge_ssh_key_path" {
  description = "Private key for the edge VPS"
  type        = string
}
variable "edge_ip" {
  description = "IPv4 the DNS record points at (the edge VPS)"
  type        = string
  default     = "66.94.101.153"
}
variable "public_host" {
  description = "Public FQDN served by the edge"
  type        = string
  default     = "qwen.rangeltech.net"
}
variable "profile" {
  description = "fp8 (default, BF16 checkpoint quantised at load) or nvfp4"
  type        = string
  default     = "fp8"
  validation {
    condition     = contains(["fp8", "nvfp4"], var.profile)
    error_message = "profile must be fp8 or nvfp4."
  }
}
variable "max_model_len" {
  type    = number
  default = 160000
}
variable "prune_unused" {
  description = "Delete the other profile's weights from the billed disk"
  type        = bool
  default     = true
}
