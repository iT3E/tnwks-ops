## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud remote backend. Runs are driven by
## .github/workflows/terraform-{plan,apply}.yaml on changes under this directory.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    #look for PII leak here
    workspaces {
      name = "tnwks-ops-aws-prod"
    }
  }
  required_version = ">= 1.2.2"
}
