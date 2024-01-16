
## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers. https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_accounts_access.html#orgs_manage_accounts_access-cross-account-role
## "The scope of access for this role includes all principals in the management account,"
## ---------------------------------------------------------------------------------------------------------------------
provider "aws" {
  # assume_role {
  #   role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  # }
}

## ---------------------------------------------------------------------------------------------------------------------
## TERRAFORM
## Contains the configuration for Terraform Cloud, along with the required providers.
##
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
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.0.0"
    }
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Contains any data blocks that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

## ---------------------------------------------------------------------------------------------------------------------
## SES
## Contains configuration for AWS SES.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_sesv2_email_identity" "example" {
  email_identity = data.sops_file.secrets.data["test_sops_var"]
}

