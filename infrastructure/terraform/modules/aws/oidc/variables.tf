## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Supplied by the calling environment. The TFC identifiers below are only
## rendered into the role's trust-policy sub claim as strings, so this module
## does not depend on the TFC resources themselves.
## ---------------------------------------------------------------------------------------------------------------------

variable "tfc_organization" {
  description = "Terraform Cloud organization name"
  type        = string
}

variable "tfc_project_name" {
  description = "Terraform Cloud project whose workspaces may assume the role"
  type        = string
}

variable "tfc_workspace_names" {
  description = "Workspaces allowed to assume the role via OIDC"
  type        = list(string)
}
