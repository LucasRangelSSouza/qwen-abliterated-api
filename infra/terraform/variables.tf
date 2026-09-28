variable "ssh_host" {
  description = "Public IPv4 or DNS name of the target Ubuntu GPU VM."
  type        = string
}

variable "ssh_user" {
  description = "SSH user with passwordless sudo."
  type        = string
  default     = "root"
}

variable "ssh_private_key_path" {
  description = "Absolute path to the private SSH key; never commit this file."
  type        = string
  sensitive   = true
}

variable "api_domain" {
  description = "Hostinger DNS hostname delegated to this VM."
  type        = string
}

variable "vllm_api_key" {
  description = "Bearer secret required by vLLM."
  type        = string
  sensitive   = true
}

variable "model_id" {
  type    = string
  default = "nasmtrcs/Qwen3.8-27B-OBLITERATED"
}

variable "served_model_name" {
  type    = string
  default = "qwen-abliterated"
}

variable "vllm_image" {
  type    = string
  default = "vllm/vllm-openai:latest"
}

