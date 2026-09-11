## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud remote backend. CLI-driven workspace — this root module is
## run by hand during initial org bootstrap, not from CI.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-ops-aws-init"
    }
  }
  required_version = "~> 1.15.0"
}
