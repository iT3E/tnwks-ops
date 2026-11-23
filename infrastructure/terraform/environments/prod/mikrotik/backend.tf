## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud stores state. The self-hosted runner plans pull requests
## and applies on merge to main. Local tasks are for break-glass use.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-mikrotik-prod"
    }
  }
  required_version = ">= 1.9"
}
