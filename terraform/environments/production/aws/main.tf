terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    #look for PII leak here
    workspaces {
      name = "tnwks-aws-prod"
    }
  }
    required_version = ">= 1.2.2"
   required_providers {
     aws = {
       source  = "hashicorp/aws"
       version = "~> 3.27"
     }
     sops = {
       source  = "carlpett/sops"
       version = "0.7.2"
   }
 }
}

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

module "kms_sops" {
  source = "./kms/"
}

module "iam_role_itadmin" {
  source = "./iam/roles"
}

module "iam_role_sops" {
  source = "./iam/roles"
}
