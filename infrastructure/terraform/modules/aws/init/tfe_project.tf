## ---------------------------------------------------------------------------------------------------------------------
## TFE PROJECT
## Terraform Cloud project grouping the AWS workspaces.
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_project" "tfe_project_aws" {
  organization = "tnwks-ops"
  name         = "AWS_Project"
}
