## ---------------------------------------------------------------------------------------------------------------------
## LOCALS
## Shared by both module calls. The project name is also rendered into the
## OIDC role's trust policy, so both modules must agree on it.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  tfc_organization = "tnwks-ops"
  tfc_project_name = "AWS_Project"
}
