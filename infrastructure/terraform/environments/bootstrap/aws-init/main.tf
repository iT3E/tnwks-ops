## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Org bootstrap, applied by hand before anything else exists.
##
## Two calls because the resources belong to two different providers:
## the AWS-side OIDC trust, and the Terraform Cloud project/workspaces that
## assume it. aws_oidc is created first and its role ARN is published as a
## TFC variable by the tfc module.
## ---------------------------------------------------------------------------------------------------------------------

module "aws_oidc" {
  source = "../../../modules/aws/oidc"

  tfc_organization = local.tfc_organization
  tfc_project_name = local.tfc_project_name

  tfc_workspace_names = [
    "tnwks-ops-aws-identity",
    "tnwks-ops-aws-prod",
  ]
}

module "tfc" {
  source = "../../../modules/tfc"

  tfc_organization       = local.tfc_organization
  project_name           = local.tfc_project_name
  aws_oidc_role_arn      = module.aws_oidc.role_arn
  aws_account_prod_email = data.sops_file.secrets.data["aws_account_prod_email"]
}
