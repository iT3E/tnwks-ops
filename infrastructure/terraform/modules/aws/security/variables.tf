## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Supplied by the calling environment.
## ---------------------------------------------------------------------------------------------------------------------

variable "rolesanywhere_trust_anchor_arn" {
  description = "ARN of the IAM Roles Anywhere trust anchor (modules/aws/rolesanywhere.tf) that the two reader roles trust."
  type        = string
}

variable "rolesanywhere_audit_common_names" {
  description = "Certificate CNs allowed a Roles Anywhere session as the audit role (read-only on both accounts). Empty removes the role."
  type        = list(string)
  default     = []
}

variable "rolesanywhere_trail_common_names" {
  description = "Certificate CNs allowed a Roles Anywhere session as the trail reader role (read and list on the trail bucket). Empty removes the role."
  type        = list(string)
  default     = []
}
