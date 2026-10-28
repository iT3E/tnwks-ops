## ---------------------------------------------------------------------------------------------------------------------
## IAM ROLES
## Assumable role granting SOPS decrypt access.
## ---------------------------------------------------------------------------------------------------------------------

module "iam_assumable_role_sops" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "~> 6.0"

  name            = "iam-role-sops"
  use_name_prefix = false
  description     = "Allows use of SOPS KMS key and allows assumption of role by itadmin"

  # v6 builds the trust policy from inputs; feeding the existing document in
  # unchanged keeps the role's assume_role_policy identical.
  source_trust_policy_documents = [data.aws_iam_policy_document.custom_role_trust_policy.json]

  policies = {
    kms_sops = module.iam_policy_kms_sops.arn
  }

  tags = local.tags
}

# iam v5 -> v6: the iam-assumable-role submodule became iam-role, and its
# count-indexed custom policy attachment became a map keyed by name.
moved {
  from = module.iam_assumable_role_sops.aws_iam_role_policy_attachment.custom[0]
  to   = module.iam_assumable_role_sops.aws_iam_role_policy_attachment.this["kms_sops"]
}
