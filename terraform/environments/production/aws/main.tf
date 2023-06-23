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


#####################################
##                                 ##
##        KMS Key - Sops           ##
##                                 ##
#####################################

data "aws_iam_policy_document" "kms_key_policy" {
  statement {
    sid    = "EnableIAMUserPermissions"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [module.iam_role.iam_role_sops.role_arn]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }
}

module "kms_sops" {
  source          = "../../../modules/aws/kms"
  key_description = "KMS Key for SOPS"
  key_policy      = data.aws_iam_policy_document.kms_key_policy.json
}


########################
##                    ##
## IAM Role - itadmin ##
##                    ##
########################

module "iam_role_itadmin" {
  source             = "../../../modules/aws/iam/roles"
  iam_role_name               = "itadmin_role"
}

########################
##                    ##
##  IAM Role - sops   ##
##                    ##
########################

data "aws_iam_policy_document" "assume_role_policy" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [module.iam_role.itadmin.role_arn]
    }
  }
}

module "iam_role_sops" {
  source             = "../../../modules/aws/iam/roles"
  iam_role_name               = "sops_role"
  assume_role_policy = data.aws_iam_policy_document.assume_role_policy.json
}

#####################################
##                                 ##
##  IAM Policy - KMS Access Sops   ##
##                                 ##
#####################################

data "aws_iam_policy_document" "kms_access_policy" {
  statement {
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [module.kms_sops.key_arn]
  }
}

module "kms_access_policy" {
  source          = "../../../modules/aws/iam/policies"
  policy_name     = "kms_sops_key_policy"
  policy_json     = data.aws_iam_policy_document.kms_access_policy.json
}
