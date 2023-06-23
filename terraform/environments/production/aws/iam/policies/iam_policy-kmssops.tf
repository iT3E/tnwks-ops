data "aws_iam_policy_document" "kms_access_policy" {
  statement {
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [module.aws_kms_key.kms_sops.kms_key_arn]
  }
}

module "kms_access_policy" {
  source          = "../../../../modules/aws/iam/policies"
  policy_name     = "kms_sops_key_policy"
  policy_json     = data.aws_iam_policy_document.kms_access_policy.json
}
