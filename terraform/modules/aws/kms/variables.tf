variable "key_description" {
  description = "Description for the KMS key"
  type        = string
}

variable "key_policy" {
  description = "KMS Key Policy in JSON"
  type        = string
}
