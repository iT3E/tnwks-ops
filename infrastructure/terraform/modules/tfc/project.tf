## ---------------------------------------------------------------------------------------------------------------------
## PROJECT
## Terraform Cloud project grouping the AWS workspaces.
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_project" "tfe_project_aws" {
  organization = var.tfc_organization
  name         = var.project_name
}
