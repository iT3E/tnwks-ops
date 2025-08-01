## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  region     = data.sops_file.secrets.data["aws_region"]
  access_key = data.sops_file.secrets.data["aws_access_key"]
  secret_key = data.sops_file.secrets.data["aws_secret_key"]
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
    workspaces {
      name = "tnwks-ops-aws-init"
    }
  }
  required_version = "~> 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
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

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

data "tls_certificate" "tfc_certificate" {
  url = "https://app.terraform.io"
}

## ---------------------------------------------------------------------------------------------------------------------
## TF CLOUD PROJECT
## Contains all Terraform Cloud projects.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_project" "tfe_project_aws" {
  organization = "tnwks-ops"
  name         = "AWS_Project"
}

## ---------------------------------------------------------------------------------------------------------------------
## TF WORKSPACES - AWS
## Contains all Terraform Workspaces for use with AWS.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name         = "tnwks-ops-aws-identity"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-identity_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-identity.id
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name         = "tnwks-ops-aws-prod"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-prod.id
  execution_mode = "remote"
}

## ---------------------------------------------------------------------------------------------------------------------
## TERRAFORM OIDC
## Configures Terraform OIDC role to be used for all AWS actions.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "tfc_provider" {
  url             = data.tls_certificate.tfc_certificate.url
  client_id_list  = ["aws.workload.identity"]
  thumbprint_list = [data.tls_certificate.tfc_certificate.certificates[0].sha1_fingerprint]
}

resource "aws_iam_role" "tfc_oidc_role" {
  name = "tfc-oidc-role"
  inline_policy {
    name = "my_inline_policy"

    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Action   = ["*"]
          Effect   = "Allow"
          Resource = "*"
        },
      ]
    })
  }

  assume_role_policy = <<EOF
{
 "Version": "2012-10-17",
 "Statement": [
   {
     "Effect": "Allow",
     "Principal": {
       "Federated": "${aws_iam_openid_connect_provider.tfc_provider.arn}"
     },
     "Action": "sts:AssumeRoleWithWebIdentity",
     "Condition": {
       "StringEquals": {
         "app.terraform.io:aud": "${one(aws_iam_openid_connect_provider.tfc_provider.client_id_list)}"
       },
       "StringLike": {
         "app.terraform.io:sub": [
          "organization:tnwks-ops:project:${tfe_project.tfe_project_aws.name}:workspace:tnwks-ops-aws-identity:run_phase:*",
          "organization:tnwks-ops:project:${tfe_project.tfe_project_aws.name}:workspace:tnwks-ops-aws-prod:run_phase:*"
         ]
       }
     }
   }
 ]
}
EOF
}

## ---------------------------------------------------------------------------------------------------------------------
## TF CLOUD VARIABLE SET
## Contains variable set and variables to be applied to mulitple workspaces
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_variable_set" "variable_set" {
  name         = "aws_var_set"
  description  = "Variable set containing AWS connectivity settings"
  organization = "tnwks-ops"
}

resource "tfe_project_variable_set" "variable_set_project" {
  variable_set_id = tfe_variable_set.variable_set.id
  project_id      = tfe_project.tfe_project_aws.id
}

resource "tfe_variable" "tfe_var_aws_auth_bool" {
  key             = "TFC_AWS_PROVIDER_AUTH"
  value           = "true"
  category        = "env"
  description     = "Determines if TFC will use the OIDC role"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_aws_auth_arn" {
  key             = "TFC_AWS_RUN_ROLE_ARN"
  value           = aws_iam_role.tfc_oidc_role.arn
  category        = "env"
  description     = "Role to be used for OIDC auth"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_aws_region" {
  key             = "AWS_REGION"
  value           = "us-west-2"
  category        = "env"
  description     = "AWS region to be used"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_sens_email" {
  key             = "aws_account_prod_email"
  value           = data.sops_file.secrets.data["aws_account_prod_email"]
  category        = "terraform"
  description     = "AWS region to be used"
  variable_set_id = tfe_variable_set.variable_set.id
  sensitive       = true
}
