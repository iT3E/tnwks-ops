## ---------------------------------------------------------------------------------------------------------------------
## MOVED
## Resources were relocated into modules/aws without changing any
## configuration. These blocks tell Terraform to update the state
## addresses in place instead of destroying and recreating.
##
## Safe to delete once applied in every workspace.
## ---------------------------------------------------------------------------------------------------------------------

moved {
  from = aws_acm_certificate.auth_tnwks_us
  to   = module.aws.aws_acm_certificate.auth_tnwks_us
}

moved {
  from = aws_acm_certificate_validation.auth_tnwks_us
  to   = module.aws.aws_acm_certificate_validation.auth_tnwks_us
}

moved {
  from = aws_cognito_resource_server.tnwks_api
  to   = module.aws.aws_cognito_resource_server.tnwks_api
}

moved {
  from = aws_cognito_user.admin
  to   = module.aws.aws_cognito_user.admin
}

moved {
  from = aws_cognito_user.viewer
  to   = module.aws.aws_cognito_user.viewer
}

moved {
  from = aws_cognito_user_group.admins
  to   = module.aws.aws_cognito_user_group.admins
}

moved {
  from = aws_cognito_user_group.agents
  to   = module.aws.aws_cognito_user_group.agents
}

moved {
  from = aws_cognito_user_group.viewers
  to   = module.aws.aws_cognito_user_group.viewers
}

moved {
  from = aws_cognito_user_in_group.admin_in_admins
  to   = module.aws.aws_cognito_user_in_group.admin_in_admins
}

moved {
  from = aws_cognito_user_in_group.viewer_in_viewers
  to   = module.aws.aws_cognito_user_in_group.viewer_in_viewers
}

moved {
  from = aws_cognito_user_pool.tnwks_auth
  to   = module.aws.aws_cognito_user_pool.tnwks_auth
}

moved {
  from = aws_cognito_user_pool_client.agent_ori
  to   = module.aws.aws_cognito_user_pool_client.agent_ori
}

moved {
  from = aws_cognito_user_pool_client.oauth2_proxy
  to   = module.aws.aws_cognito_user_pool_client.oauth2_proxy
}

moved {
  from = aws_cognito_user_pool_client.onboard
  to   = module.aws.aws_cognito_user_pool_client.onboard
}

moved {
  from = aws_cognito_user_pool_domain.tnwks_auth
  to   = module.aws.aws_cognito_user_pool_domain.tnwks_auth
}

moved {
  from = aws_iam_access_key.smtp_user
  to   = module.aws.aws_iam_access_key.smtp_user
}

moved {
  from = aws_iam_policy.ses_sender
  to   = module.aws.aws_iam_policy.ses_sender
}

moved {
  from = aws_iam_user.smtp_user
  to   = module.aws.aws_iam_user.smtp_user
}

moved {
  from = aws_iam_user_policy_attachment.test-attach
  to   = module.aws.aws_iam_user_policy_attachment.test-attach
}

moved {
  from = aws_ses_domain_dkim.ses_domain_dkim
  to   = module.aws.aws_ses_domain_dkim.ses_domain_dkim
}

moved {
  from = aws_ses_domain_identity.ses_domain_identity
  to   = module.aws.aws_ses_domain_identity.ses_domain_identity
}

moved {
  from = random_password.admin_temp
  to   = module.aws.random_password.admin_temp
}

moved {
  from = random_password.viewer_temp
  to   = module.aws.random_password.viewer_temp
}

moved {
  from = terraform_data.cognito_mfa
  to   = module.aws.terraform_data.cognito_mfa
}

moved {
  from = terraform_data.oauth2_proxy_managed_login
  to   = module.aws.terraform_data.oauth2_proxy_managed_login
}

moved {
  from = terraform_data.onboard_managed_login
  to   = module.aws.terraform_data.onboard_managed_login
}
