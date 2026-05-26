variable "aws_region" {
  description = "AWS region for the Cognito user pool."
  type        = string
  default     = "us-east-1"
}

variable "hosted_ui_prefix" {
  description = "Prefix for the Cognito hosted UI domain (becomes <prefix>.auth.<region>.amazoncognito.com). Ignored when custom_domain is set."
  type        = string
  default     = "tnwks-auth"
}

variable "custom_domain" {
  description = "Optional custom hosted UI domain (e.g. auth.tnwks.us). Requires acm_cert_arn (ACM cert in us-east-1) and a parent A record on the apex."
  type        = string
  default     = null
}

variable "acm_cert_arn" {
  description = "ACM certificate ARN for custom_domain. Must be in us-east-1 regardless of pool region."
  type        = string
  default     = null
}

variable "relying_party_id" {
  description = "WebAuthn relying party ID. Should be the parent domain users authenticate against (e.g. internal.tnwks.us)."
  type        = string
  default     = "internal.tnwks.us"
}
