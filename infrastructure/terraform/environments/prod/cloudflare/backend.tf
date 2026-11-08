## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud holds the state; runs execute where Terraform is invoked
## (workspace execution mode `local`), which is the self-hosted
## tnwks-ops-runner: terraform.yaml plans on PRs (ci.yaml), applies on
## merge. `task terraform:plan|apply` is break-glass only.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-cloudflare-prod"
    }
  }
  required_version = ">= 1.2.2"
}
