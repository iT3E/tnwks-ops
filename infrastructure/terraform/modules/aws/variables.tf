## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Values the calling environment must supply. Secrets are read from SOPS in
## the environment root and passed in, so this module stays free of any
## assumption about where secrets live on disk.
## ---------------------------------------------------------------------------------------------------------------------

variable "admin_email" {
  description = "Email address for the seeded Cognito admin user"
  type        = string
  sensitive   = true
}

variable "viewer_email" {
  description = "Email address for the seeded Cognito viewer user"
  type        = string
  sensitive   = true
}

variable "ses_domain" {
  description = "Domain to verify as an SES sending identity"
  type        = string
}

variable "rolesanywhere_ca_pem" {
  description = "PEM certificate of the CA trusted by IAM Roles Anywhere (public). Null disables Roles Anywhere."
  type        = string
  default     = null
}

variable "rolesanywhere_sops_common_names" {
  description = "Certificate CNs allowed to get a SOPS session through Roles Anywhere."
  type        = list(string)
  default     = []
}

variable "sops_role_arn" {
  description = "ARN of the management account's iam-role-sops, the only role the Roles Anywhere role may assume."
  type        = string
  default     = null
}
