
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
      name = "tnwks-aws-prod"
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
       version = "1.0.0"
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

data "aws_caller_identity" "current" {}

## ---------------------------------------------------------------------------------------------------------------------
## LOCALS
## Contains any locals that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

locals {
  tags = {
    Environment = "prod"
    # Add more tags as needed.
  }
}


## ---------------------------------------------------------------------------------------------------------------------
## KMS KEY POLICY
## Contains key policy which allows sops role to use the key, along with management of the key by the it-admin role.
##
## ---------------------------------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "kms_key_policy" {
  statement {
    sid    = "Allow use of the key"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [module.iam_role_sops.role_arn]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }
  statement {
    sid    = "AllowKeyPolicyUpdates"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [module.iam_role_itadmin.role_arn]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## KMS KEY
## This creates a KMS key that provides the "kms_sops" role full permissions on it, along with the terraform executor.
##
## ---------------------------------------------------------------------------------------------------------------------

module "kms_ec2" {
  source = "terraform-aws-modules/kms/aws"
  version = "~> 2.0"

  deletion_window_in_days = 7
  description             = "Used by sops"
  enable_key_rotation     = false
  is_enabled              = true
  key_usage               = "ENCRYPT_DECRYPT"
  multi_region            = true

  key_statements = [
    {
      sid    = "Allow administration of the key"
      effect = "Allow"

      principals = [
        {
          type        = "AWS"
          identifiers = [module.iam_role_itadmin.role_arn]
        }
      ]

      actions = [
        "kms:Create*",
        "kms:Describe*",
        "kms:Enable*",
        "kms:List*",
        "kms:Put*",
        "kms:Update*",
        "kms:Revoke*",
        "kms:Disable*",
        "kms:Get*",
        "kms:Delete*",
        "kms:TagResource",
        "kms:UntagResource",
        "kms:ScheduleKeyDeletion",
        "kms:CancelKeyDeletion",
      ]

      resources = ["*"]
    },
    {
      sid    = "Allow use of the key"
      effect = "Allow"

      principals = [
        {
          type        = "AWS"
          identifiers = [module.iam_role_sops.role_arn]
        }
      ]
      actions = [
        "kms:Encrypt",
        "kms:Decrypt",
        "kms:ReEncrypt*",
        "kms:GenerateDataKey*",
        "kms:DescribeKey"
      ]
      resources = ["*"]
    }
  ]
  # Aliases
  aliases = ["kms-sops"]

  tags = local.tags
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLE
## This creates an IAM Role "itadmin" and allows EC2 to assume this role.
##
## ---------------------------------------------------------------------------------------------------------------------

module "iam_role_itadmin" {
  source             = "../../../modules/aws/iam/roles"
  iam_role_name               = "itadmin_role"
  assume_role_policy = data.aws_iam_policy_document.assume_role_policy_itadmin.json
}

data "aws_iam_policy_document" "assume_role_policy_itadmin" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}
## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLE
## This creates an IAM Role "sops_role" and allows the executor of this terraform to assume the role, along with the
## "itadmin" role. Additionally one custom IAM policy is attached which grants KMS usage permissions.
## ---------------------------------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "assume_role_policy_sops" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [module.iam_role_itadmin.role_arn]
    }
  }
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [data.aws_caller_identity.current.arn]
    }
  }
}

module "iam_role_sops" {
  source             = "../../../modules/aws/iam/roles"
  iam_role_name               = "sops_role"
  assume_role_policy = data.aws_iam_policy_document.assume_role_policy_sops.json
}

module "sops_kms_access_policy_attachment" {
  source     = "../../../modules/aws/iam/policy_attachments"
  role_name  = module.iam_role_sops.iam_role_name
  policy_arn = module.kms_access_policy.policy_arn
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICY
## This IAM Policy allows KMS usage actions.
##
## ---------------------------------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "kms_access_policy" {
  statement {
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [module.kms_sops.kms_key_arn]
  }
}

module "kms_access_policy" {
  source          = "../../../modules/aws/iam/policies"
  policy_name     = "kms_sops_key_policy"
  policy_json     = data.aws_iam_policy_document.kms_access_policy.json
}
