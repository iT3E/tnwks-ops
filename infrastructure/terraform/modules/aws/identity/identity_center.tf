## ---------------------------------------------------------------------------------------------------------------------
## IDENTITY CENTER
## SSO users, groups, permission sets and account assignments.
## ---------------------------------------------------------------------------------------------------------------------

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
