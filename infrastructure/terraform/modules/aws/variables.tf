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

variable "kiosk_email" {
  description = "Email address (username) for the shared kiosk identity: the kitchen wall display, signed in as a read-only viewer"
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

variable "rolesanywhere_backups_sites" {
  description = <<-EOT
    Backup roles for other sites (rolesanywhere.tf), keyed by site (wsl-02): the certificate CNs allowed a session,
    and the bucket key patterns that site may write and delete (it can read everything).
  EOT
  type = map(object({
    common_names   = list(string)
    write_prefixes = list(string)
  }))
  default = {}
}

variable "rolesanywhere_backups_common_names" {
  description = "Certificate CNs allowed a Roles Anywhere session as the homelab backup role (S3 read/write on the backup bucket)."
  type        = list(string)
  default     = []
}
