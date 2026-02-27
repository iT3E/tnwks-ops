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
      name = "tnwks-ops-aws-identity"
    }
  }
  required_version = ">= 1.2.2"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Contains any data blocks that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

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

resource "aws_organizations_account" "prod_aws_account" {
  name              = "tnwks-ops-aws-prod"
  email             = var.aws_account_prod_email
  close_on_deletion = true
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

locals {
  identity_store_id = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.identity_store_ids)[0]
  instance_arn      = tolist(data.aws_ssoadmin_instances.ssoadmin_instance.arns)[0]
}

resource "aws_identitystore_user" "sso_user_it_admin" {
  identity_store_id = local.identity_store_id
  display_name      = "it-admin"
  user_name         = "it-admin"

  name {
    given_name  = "it-admin"
    family_name = "it-admin"
  }

  emails {
    value = var.aws_account_prod_email
  }
}

resource "aws_identitystore_group" "sso_group_admin" {
  display_name      = "admin_group"
  identity_store_id = local.identity_store_id
}

resource "aws_identitystore_group_membership" "sso_group_membership" {
  identity_store_id = local.identity_store_id
  group_id          = aws_identitystore_group.sso_group_admin.group_id
  member_id         = aws_identitystore_user.sso_user_it_admin.user_id
}

resource "aws_ssoadmin_permission_set" "sso_admin_permission_set" {
  name         = "AdministratorAccess"
  instance_arn = local.instance_arn
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
  inline_policy      = data.aws_iam_policy_document.inline_iam_policy_adminaccess.json
  instance_arn       = aws_ssoadmin_permission_set.sso_admin_permission_set.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.sso_admin_permission_set.arn
}

resource "aws_ssoadmin_account_assignment" "sso_account_assignment_orgowner" {
  instance_arn       = aws_ssoadmin_permission_set.sso_admin_permission_set.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.sso_admin_permission_set.arn
  principal_id       = aws_identitystore_group.sso_group_admin.group_id
  principal_type     = "GROUP"
  target_id          = data.aws_caller_identity.current.account_id
  target_type        = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "sso_account_assignment_prod" {
  instance_arn       = aws_ssoadmin_permission_set.sso_admin_permission_set.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.sso_admin_permission_set.arn
  principal_id       = aws_identitystore_group.sso_group_admin.group_id
  principal_type     = "GROUP"
  target_id          = aws_organizations_account.prod_aws_account.id
  target_type        = "AWS_ACCOUNT"
}
## ---------------------------------------------------------------------------------------------------------------------
## KMS KEY
## This creates a KMS key that provides the "kms_sops" role full permissions on it, along with the terraform executor.
##
## ---------------------------------------------------------------------------------------------------------------------

module "kms_sops" {
  source  = "terraform-aws-modules/kms/aws"
  version = "~> 4.0"

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
          identifiers = ["*"]
        }
      ]
      conditions = [
        {
          test     = "StringLike"
          variable = "aws:PrincipalArn"
          values   = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/${data.aws_region.current.name}/AWSReservedSSO_AdministratorAccess_*"]
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

  create_role                     = true
  role_name                       = "iam-role-sops"
  role_description                = "Allows use of SOPS KMS key and allows assumption of role by itadmin"
  role_requires_mfa               = false
  create_custom_role_trust_policy = true
  custom_role_trust_policy        = data.aws_iam_policy_document.custom_role_trust_policy.json
  custom_role_policy_arns         = [module.iam_policy_kms_sops.arn]

  tags = local.tags
}

data "aws_iam_policy_document" "custom_role_trust_policy" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/${data.aws_region.current.name}/AWSReservedSSO_AdministratorAccess_*"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/tfc-oidc-role"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = ["arn:aws:iam::${aws_organizations_account.prod_aws_account.id}:role/tnwks-org-init-role"]
    }
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
  }
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
