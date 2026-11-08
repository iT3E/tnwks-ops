## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud holds the state; runs execute where Terraform is invoked
## (workspace execution mode `local`, modules/tfc/workspaces.tf), which is the
## self-hosted tnwks-ops-runner: terraform.yaml plans on PRs (ci.yaml),
## applies on merge (deploy.yaml). It reads the API key from SOPS with the
## CI age key. `task healthchecks:plan|apply` is break-glass only.
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
