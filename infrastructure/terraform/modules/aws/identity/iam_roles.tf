## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES
## Assumable role granting SOPS decrypt access.
## ---------------------------------------------------------------------------------------------------------------------

module "iam_assumable_role_sops" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version = "~> 6.0"

  create_role                     = true
  role_name                       = "iam-role-sops"
  role_description                = "Allows use of SOPS KMS key and allows assumption of role by itadmin"
  role_requires_mfa               = false
  create_custom_role_trust_policy = true
  custom_role_trust_policy        = data.aws_iam_policy_document.custom_role_trust_policy.json
  custom_role_policy_arns         = [module.iam_policy_kms_sops.arn]

  tags = local.tags
}
