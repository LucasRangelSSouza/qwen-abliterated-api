output "api_base_url" {
  value = "https://${var.api_domain}/v1"
}

output "model_name" {
  value = var.served_model_name
}

