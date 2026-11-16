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

variable "rolesanywhere_backups_prefixed" {
  description = <<-EOT
    Backup roles kept to one prefix of the bucket (rolesanywhere.tf), keyed by name (agents): the certificate CNs
    allowed a session, and the prefix (ending in /) that the role may list, read, write and delete. It reaches
    nothing else in the bucket. With exclusive = true, a bucket policy (s3_backups.tf) also denies that prefix to
    every other principal but backups_break_glass_role_arn_patterns.
  EOT
  type = map(object({
    common_names = list(string)
    prefix       = string
    exclusive    = optional(bool, false)
  }))
  default = {}
  validation {
    condition     = alltrue([for v in values(var.rolesanywhere_backups_prefixed) : endswith(v.prefix, "/") && length(v.prefix) > 1])
    error_message = "Each prefix must end in / and not be the bucket root."
  }
  # The list deny of an exclusive prefix (s3_backups.tf) matches the prefix
  # without its final / and anything after it, so it would also refuse a list
  # of another role whose prefix starts with that name. This check sees only
  # the entries of this variable: not the prefixes that the shared backup
  # role or the site roles write (rolesanywhere_backups_sites, kopiur/,
  # cnpg/). Compare a new exclusive prefix with those by hand.
  validation {
    condition = alltrue(flatten([
      for ka, a in var.rolesanywhere_backups_prefixed : [
        for kb, b in var.rolesanywhere_backups_prefixed : ka == kb || !startswith(b.prefix, trimsuffix(a.prefix, "/"))
      ]
    ]))
    error_message = "No prefix may start with another prefix without its final /."
  }
}

variable "backups_break_glass_role_arn_patterns" {
  description = <<-EOT
    IAM role ARNs (ArnLike patterns) that keep access to an exclusive prefix of rolesanywhere_backups_prefixed next to
    its own role: the owner's way in for a restore by hand or a delete. An exclusive prefix needs at least one.
  EOT
  type        = list(string)
  default     = []
}

variable "rolesanywhere_backups_common_names" {
  description = "Certificate CNs allowed a Roles Anywhere session as the homelab backup role (S3 read/write on the backup bucket)."
  type        = list(string)
  default     = []
}
