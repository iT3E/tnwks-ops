## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud remote backend. VCS-driven workspace, triggered after
## tnwks-ops-aws-init.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    #look for PII leak here
    workspaces {
      name = "tnwks-ops-aws-identity"
    }
  }
  required_version = ">= 1.2.2"
}
