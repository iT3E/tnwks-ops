
# Run tf /aws/init/ to set up:
#     - Terraform OIDC user in Org owner AWS Account
#     - Wait for success, then
#       - import Admin user to tf
#       - delete Admin user

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
    required_version = "~> 1.2.0"
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

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

data "tls_certificate" "tfc_certificate" {
  url = "https://app.terraform.io"
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM USER
## Imports IAM User that was manually created in bootstrap process.
##
## ---------------------------------------------------------------------------------------------------------------------


import {
  to = aws_iam_user.iam-user-tnwks-admin
  id = "iam-user-tnwks-admin"
}

resource "aws_iam_user" "iam-user-tnwks-admin" {
  name  = "iam-user-tnwks-admin"
}


## ---------------------------------------------------------------------------------------------------------------------
## TERRAFORM OIDC
## Configures Terraform OIDC user for all future Terraform VCS Workflows.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "tfc_provider" {
  url             = data.tls_certificate.tfc_certificate.url
  client_id_list  = ["aws.workload.identity"]
  thumbprint_list = [data.tls_certificate.tfc_certificate.certificates[0].sha1_fingerprint]
}

resource "aws_iam_role" "tfc_role" {
  name = "tfc-role"

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
         "app.terraform.io:sub": "organization:tnwks-ops:project:Default Project:workspace:tnwks-ops-aws-identity:run_phase:*"
       }
     }
   }
 ]
}
EOF
}

