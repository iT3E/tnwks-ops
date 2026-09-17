## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICIES
## Policy documents plus the bootstrap user's inline policy and the SOPS
## KMS policy module.
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
