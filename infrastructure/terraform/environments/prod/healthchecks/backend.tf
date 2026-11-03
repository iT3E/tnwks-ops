## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud holds the state; runs execute locally (workspace execution
## mode `local`, modules/tfc/workspaces.tf) so the API key is read from SOPS on
## the desktop, like environments/prod/cloudflare. Not wired into CI:
## `task healthchecks:plan` / `task healthchecks:apply`.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-healthchecks-prod"
    }
  }
  required_version = ">= 1.9"
}
