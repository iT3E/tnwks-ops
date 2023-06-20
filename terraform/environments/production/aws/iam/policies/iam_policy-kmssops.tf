data "aws_iam_policy_document" "kms_access_policy" {
  statement {
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [module.kms_ebs.kms_ebs_key_arn]
  }
}

module "kms_access_policy" {
  source          = "./modules/aws/iam/policies"
  policy_name     = "kms_ebs_key_policy"
  policy_json     = data.aws_iam_policy_document.kms_access_policy.json
}
