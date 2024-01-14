## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
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

resource "aws_organizations_account" "prod_aws_account" {
  name              = "tnwks-ops-aws-prod"
  email             = data.sops_file.secrets.data["aws_account_prod_email"]
  close_on_deletion = false
  role_name         = "tnwks-org-init-role"
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM USER POLICY
## Creates a 'disable-all-access' policy and attaches it to init user. This user cannot be imported due to technical
## limitations with Terraform.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_user_policy" "tnwks_init_user_policy" {
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

data "aws_ssoadmin_instances" "ssoadmin_instance" {}

resource "aws_identitystore_user" "sso_user_it_admin" {
  identity_store_id = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids)[0]
  display_name      = "it-admin"
  user_name         = "it-admin"

  name {
    given_name  = "it-admin"
    family_name = "it-admin"
  }

  emails {
    value = data
  }
}

resource "aws_identitystore_group" "sso_group_admin" {
  display_name      = "admin_group"
  identity_store_id = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids)[0]
}

resource "aws_identitystore_group_membership" "sso_group_membership" {
  identity_store_id = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids)[0]
  group_id          = aws_identitystore_group.sso_group_admin.group_id
  member_id         = aws_identitystore_user.sso_group_admin.user_id
}

resource "aws_ssoadmin_permission_set" "sso_admin_permission_set" {
  name         = "AdministratorAccess"
  instance_arn = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids[0])
}

data "aws_iam_policy_document" "inline_iam_policy_adminaccess" {
  statement {
    sid = "0"
    actions = [
      "*"
    ]
    resources = [
      "*"
    ]
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "this" {
  inline_policy      = data.aws_iam_policy_document.inline_iam_policy_adminaccess
  instance_arn       = aws_ssoadmin_permission_set.sso_admin_permission_set.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.sso_admin_permission_set.arn
}

resource "aws_ssoadmin_account_assignment" "this" {
  instance_arn       = aws_ssoadmin_permission_set.sso_admin_permission_set.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.sso_admin_permission_set.arn
  principal_id       = aws_identitystore_group.sso_group_admin.group_id
  principal_type     = "GROUP"
  target_id          = sensitive(each.value)
  target_type        = "AWS_ACCOUNT"
}

## ---------------------------------------------------------------------------------------------------------------------
## KMS KEY
## This creates a KMS key that provides the "kms_sops" role full permissions on it, along with the terraform executor.
##
## ---------------------------------------------------------------------------------------------------------------------

module "kms_sops" {
  source  = "terraform-aws-modules/kms/aws"
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

  create_role       = true
  role_name         = "iam-role-sops"
  role_description  = "Allows use of SOPS KMS key and allows assumption of role by itadmin"
  role_requires_mfa = false

  custom_role_policy_arns = [
    module.iam_policy_kms_sops.arn
  ]
  trusted_role_arns = [
    "arn:aws:sts::${data.aws_caller_identity.current.account_id}:assumed-role/AWSReservedSSO_AdministratorAccess_5fe2854a9354d357/it-admin"
  ]

  tags = local.tags
}

## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICY
## This IAM Policy allows KMS usage actions.
##
## ---------------------------------------------------------------------------------------------------------------------

module "iam_policy_kms_sops" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-policy"
  version = "~> 5.0"

  name        = "iam-policy-kms-sops"
  description = "Allows access to use SOPS KMS key"
  policy      = <<-EOF
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
