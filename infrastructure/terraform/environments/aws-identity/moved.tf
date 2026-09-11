## ---------------------------------------------------------------------------------------------------------------------
## MOVED
## Relocated into modules/aws/identity with no configuration change.
##
## Safe to delete once applied.
## ---------------------------------------------------------------------------------------------------------------------

moved {
  from = module.iam_assumable_role_sops
  to   = module.identity.module.iam_assumable_role_sops
}

moved {
  from = module.iam_policy_kms_sops
  to   = module.identity.module.iam_policy_kms_sops
}

moved {
  from = module.kms_sops
  to   = module.identity.module.kms_sops
}

moved {
  from = aws_iam_user_policy.tnwks_init_user_policy
  to   = module.identity.aws_iam_user_policy.tnwks_init_user_policy
}

moved {
  from = aws_identitystore_group.sso_group_admin
  to   = module.identity.aws_identitystore_group.sso_group_admin
}

moved {
  from = aws_identitystore_group_membership.sso_group_membership
  to   = module.identity.aws_identitystore_group_membership.sso_group_membership
}

moved {
  from = aws_identitystore_user.sso_user_it_admin
  to   = module.identity.aws_identitystore_user.sso_user_it_admin
}

moved {
  from = aws_organizations_account.prod_aws_account
  to   = module.identity.aws_organizations_account.prod_aws_account
}

moved {
  from = aws_ssoadmin_account_assignment.sso_account_assignment_orgowner
  to   = module.identity.aws_ssoadmin_account_assignment.sso_account_assignment_orgowner
}

moved {
  from = aws_ssoadmin_account_assignment.sso_account_assignment_prod
  to   = module.identity.aws_ssoadmin_account_assignment.sso_account_assignment_prod
}

moved {
  from = aws_ssoadmin_permission_set.sso_admin_permission_set
  to   = module.identity.aws_ssoadmin_permission_set.sso_admin_permission_set
}

moved {
  from = aws_ssoadmin_permission_set_inline_policy.this
  to   = module.identity.aws_ssoadmin_permission_set_inline_policy.this
}
