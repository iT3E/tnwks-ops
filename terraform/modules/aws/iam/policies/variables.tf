variable "policy_name" {
  description = "The name of the policy"
  type        = string
}

variable "policy_description" {
  description = "The description of the policy"
  type        = string
  default     = "Managed by Terraform"
}

variable "policy_json" {
  description = "The JSON policy document"
  type        = string
}
