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
