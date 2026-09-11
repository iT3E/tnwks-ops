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
