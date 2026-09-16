## ---------------------------------------------------------------------------------------------------------------------
## KMS
## Customer-managed key used to encrypt SOPS secrets in this repo.
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
