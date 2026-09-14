## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Org bootstrap: the TFC OIDC trust, the TFC project, and the workspaces the
## other environments run in. Applied by hand before anything else exists.
## ---------------------------------------------------------------------------------------------------------------------

module "init" {
  source = "../../../modules/aws/init"

  aws_account_prod_email = data.sops_file.secrets.data["aws_account_prod_email"]
}
