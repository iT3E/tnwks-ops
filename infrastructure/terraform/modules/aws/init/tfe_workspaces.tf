## ---------------------------------------------------------------------------------------------------------------------
## TFE WORKSPACES
## Workspaces and their settings. Each maps to one environment root under
## infrastructure/terraform/environments/.
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name         = "tnwks-ops-aws-identity"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-identity_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-identity.id
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name         = "tnwks-ops-aws-prod"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-prod.id
  execution_mode = "remote"
}
