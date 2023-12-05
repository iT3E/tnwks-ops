## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  profile                     = "sso"
  region                      = data.sops_file.secrets.data["aws_region"]
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
      name = "tnwks-aws-identity"
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
## ORGANIZATION
## Contains the AWS Organizations configuration.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_organizations_organization" "org" {
  aws_service_access_principals = [
    "cloudtrail.amazonaws.com",
    "config.amazonaws.com",
  ]

  feature_set = "ALL"
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM USER POLICY
## Creates a 'disable-all-access' policy and attaches it to init user. This user cannot be imported due to technical
## limitations with Terraform.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_user_policy" "lb_ro" {
  name = "disable-all-access"
  user = "tnwks-init-user"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "*",
        ]
        Effect   = "Deny"
        Resource = "*"
      },
    ]
  })
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM IDENTITY CENTER
## Contains the IAM Identity Center configuration.
##
## ---------------------------------------------------------------------------------------------------------------------


## ---------------------------------------------------------------------------------------------------------------------
## PERMISSION SET
## Contains the Permission Set for IAM Identity Center.
##
## ---------------------------------------------------------------------------------------------------------------------

























## ---------------------------------------------------------------------------------------------------------------------
## KMS KEY
## This creates a KMS key that provides the "kms_sops" role full permissions on it, along with the terraform executor.
##
## ---------------------------------------------------------------------------------------------------------------------

module "kms_sops" {
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
          identifiers = ["arn:aws:sts::${data.aws_caller_identity.current.account_id}:assumed-role/AWSReservedSSO_AdministratorAccess_5fe2854a9354d357/it-admin"]

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
          identifiers = [module.iam_assumable_role_sops.iam_role_arn]
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
## Creates one IAM role that grants access to SOPS KMS key. it-admin SSO identity is allowed to assume this role.
##
## ---------------------------------------------------------------------------------------------------------------------

module "iam_assumable_role_sops" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version = "~> 5.0"

  create_role = true
  role_name   = "iam-role-sops"
  role_description = "Allows use of SOPS KMS key and allows assumption of role by itadmin"
  role_requires_mfa = false

  custom_role_policy_arns = [
    module.iam_policy_kms_sops.arn
  ]
  trusted_role_arns = [
    "arn:aws:sts::${data.aws_caller_identity.current.account_id}:assumed-role/AWSReservedSSO_AdministratorAccess_5fe2854a9354d357/it-admin"
  ]

  tags  = local.tags
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICY
## This IAM Policy allows KMS usage actions.
##
## ---------------------------------------------------------------------------------------------------------------------

module "iam_policy_kms_sops" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-policy"
  version = "~> 5.0"

  name = "iam-policy-kms-sops"
  description = "Allows access to use SOPS KMS key"
  policy = <<-EOF
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:DescribeKey"
            ],
          "Resource": "${module.kms_sops.key_arn}"
        }
      ]
    }
    EOF

  tags = local.tags
}
