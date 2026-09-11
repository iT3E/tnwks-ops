## ---------------------------------------------------------------------------------------------------------------------
## ORGANIZATION
## Contains the AWS Organizations configuration.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_organizations_account" "prod_aws_account" {
  name              = "tnwks-ops-aws-prod"
  email             = var.aws_account_prod_email
  close_on_deletion = true
  role_name         = "tnwks-org-init-role"
}
