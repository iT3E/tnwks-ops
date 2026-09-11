## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud remote backend. Not wired into CI — this root module is
## applied locally via `task terraform:apply` after a PR merges.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-cloudflare-prod_old"
    }
  }
  required_version = ">= 1.2.2"
}
