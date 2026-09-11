## ---------------------------------------------------------------------------------------------------------------------
## TF CLOUD PROJECT
## Contains all Terraform Cloud projects.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_project" "tfe_project_aws" {
  organization = "tnwks-ops"
  name         = "AWS_Project"
}

## ---------------------------------------------------------------------------------------------------------------------
## TF WORKSPACES - AWS
## Contains all Terraform Workspaces for use with AWS.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_workspace" "tnwks-ops-aws-identity" {
  name         = "tnwks-ops-aws-identity"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-identity_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-identity.id
  execution_mode = "remote"
}

resource "tfe_workspace" "tnwks-ops-aws-prod" {
  name         = "tnwks-ops-aws-prod"
  organization = "tnwks-ops"
  project_id   = tfe_project.tfe_project_aws.id
}

resource "tfe_workspace_settings" "tnwks-ops-aws-prod_workspace_settings" {
  workspace_id   = tfe_workspace.tnwks-ops-aws-prod.id
  execution_mode = "remote"
}

## ---------------------------------------------------------------------------------------------------------------------
## TF CLOUD VARIABLE SET
## Contains variable set and variables to be applied to mulitple workspaces
##
## ---------------------------------------------------------------------------------------------------------------------

resource "tfe_variable_set" "variable_set" {
  name         = "aws_var_set"
  description  = "Variable set containing AWS connectivity settings"
  organization = "tnwks-ops"
}

resource "tfe_project_variable_set" "variable_set_project" {
  variable_set_id = tfe_variable_set.variable_set.id
  project_id      = tfe_project.tfe_project_aws.id
}

resource "tfe_variable" "tfe_var_aws_auth_bool" {
  key             = "TFC_AWS_PROVIDER_AUTH"
  value           = "true"
  category        = "env"
  description     = "Determines if TFC will use the OIDC role"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_aws_auth_arn" {
  key             = "TFC_AWS_RUN_ROLE_ARN"
  value           = aws_iam_role.tfc_oidc_role.arn
  category        = "env"
  description     = "Role to be used for OIDC auth"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_aws_region" {
  key             = "AWS_REGION"
  value           = "us-west-2"
  category        = "env"
  description     = "AWS region to be used"
  variable_set_id = tfe_variable_set.variable_set.id
}

resource "tfe_variable" "tfe_var_sens_email" {
  key             = "aws_account_prod_email"
  value           = data.sops_file.secrets.data["aws_account_prod_email"]
  category        = "terraform"
  description     = "AWS region to be used"
  variable_set_id = tfe_variable_set.variable_set.id
  sensitive       = true
}
