## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud holds the state; runs execute locally (workspace execution
## mode `local`, modules/tfc/workspaces.tf) because the controller is only
## reachable from inside the LAN. Same model as environments/prod/mikrotik.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-unifi-prod"
    }
  }
  required_version = ">= 1.9"
}
