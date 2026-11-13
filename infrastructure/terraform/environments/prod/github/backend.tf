## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud holds the state; runs execute where Terraform is invoked
## (workspace execution mode `local`, modules/tfc/workspaces.tf), which is the
## self-hosted tnwks-ops-runner: terraform.yaml plans on PRs (ci.yaml),
## applies on merge (deploy.yaml), once GITHUB_TERRAFORM_ENABLED is true. It
## reads the token from SOPS with the CI age key. `task github:plan|apply` is
## break-glass only.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-github-prod"
    }
  }
  required_version = ">= 1.9"
}
