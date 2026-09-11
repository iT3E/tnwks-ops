## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## AWS Organizations, Identity Center and the KMS key backing SOPS.
## ---------------------------------------------------------------------------------------------------------------------

module "identity" {
  source = "../../modules/aws/identity"

  aws_account_prod_email = var.aws_account_prod_email
}
