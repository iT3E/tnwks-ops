## ---------------------------------------------------------------------------------------------------------------------
## TFE WORKSPACES
## Workspaces and their settings. Each maps to one environment root under
## infrastructure/terraform/environments/.
##
## working_directory is a path relative to the root of the uploaded
## configuration directory, which is infrastructure/terraform, so the value is
## environments/<root> and never the full repo path. Leaving it unset makes
## Terraform Cloud run from the configuration root, where no backend or
## provider config exists, so every run for that workspace fails.
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name              = "tnwks-ops-aws-identity"
  organization      = var.tfc_organization
  project_id        = tfe_project.tfe_project_aws.id
  working_directory = "environments/bootstrap/aws-identity"
}

resource "tfe_workspace_settings" "tnwks-ops-aws-identity_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-identity.id
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name              = "tnwks-ops-aws-prod"
  organization      = var.tfc_organization
  project_id        = tfe_project.tfe_project_aws.id
  working_directory = "environments/prod/aws"
}

resource "tfe_workspace_settings" "tnwks-ops-aws-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-prod.id
  execution_mode = "remote"
}

# RouterOS is reachable only from inside the LAN, so Terraform Cloud holds the
# state and the run executes wherever `terraform` is invoked (a workstation, or
# later a self-hosted runner). Remote execution could never reach the router.
resource "tfe_workspace" "tnwks-mikrotik-prod" {
  name              = "tnwks-mikrotik-prod"
  organization      = var.tfc_organization
  project_id        = tfe_project.tfe_project_aws.id
  working_directory = "environments/prod/mikrotik"
}

resource "tfe_workspace_settings" "tnwks-mikrotik-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-mikrotik-prod.id
  execution_mode = "local"
}

# The UniFi controller (networking/unifi on homelab-wsl) is reachable only from
# the LAN, so this runs locally like the MikroTik workspace.
resource "tfe_workspace" "tnwks-unifi-prod" {
  name              = "tnwks-unifi-prod"
  organization      = var.tfc_organization
  project_id        = tfe_project.tfe_project_aws.id
  working_directory = "environments/prod/unifi"
}

resource "tfe_workspace_settings" "tnwks-unifi-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-unifi-prod.id
  execution_mode = "local"
}
