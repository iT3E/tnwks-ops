## ---------------------------------------------------------------------------------------------------------------------
## MOVED
## Relocated out of the root module with no configuration change. These
## rewrite state addresses in place instead of destroying and recreating.
##
## The AWS-side OIDC trust and the Terraform Cloud project/workspaces went to
## separate provider-scoped modules, so the two groups have different targets.
##
## Safe to delete once applied.
## ---------------------------------------------------------------------------------------------------------------------

moved {
  from = aws_iam_openid_connect_provider.tfc_provider
  to   = module.aws_oidc.aws_iam_openid_connect_provider.tfc_provider
}

moved {
  from = aws_iam_role.tfc_oidc_role
  to   = module.aws_oidc.aws_iam_role.tfc_oidc_role
}

moved {
  from = tfe_project.tfe_project_aws
  to   = module.tfc.tfe_project.tfe_project_aws
}

moved {
  from = tfe_project_variable_set.variable_set_project
  to   = module.tfc.tfe_project_variable_set.variable_set_project
}

moved {
  from = tfe_variable.tfe_var_aws_auth_arn
  to   = module.tfc.tfe_variable.tfe_var_aws_auth_arn
}

moved {
  from = tfe_variable.tfe_var_aws_auth_bool
  to   = module.tfc.tfe_variable.tfe_var_aws_auth_bool
}

moved {
  from = tfe_variable.tfe_var_aws_region
  to   = module.tfc.tfe_variable.tfe_var_aws_region
}

moved {
  from = tfe_variable.tfe_var_sens_email
  to   = module.tfc.tfe_variable.tfe_var_sens_email
}

moved {
  from = tfe_variable_set.variable_set
  to   = module.tfc.tfe_variable_set.variable_set
}

moved {
  from = tfe_workspace.tnwks-ops-aws-identity
  to   = module.tfc.tfe_workspace.tnwks-ops-aws-identity
}

moved {
  from = tfe_workspace.tnwks-ops-aws-prod
  to   = module.tfc.tfe_workspace.tnwks-ops-aws-prod
}

moved {
  from = tfe_workspace_settings.tnwks-ops-aws-identity_workspace_settings
  to   = module.tfc.tfe_workspace_settings.tnwks-ops-aws-identity_workspace_settings
}

moved {
  from = tfe_workspace_settings.tnwks-ops-aws-prod_workspace_settings
  to   = module.tfc.tfe_workspace_settings.tnwks-ops-aws-prod_workspace_settings
}
