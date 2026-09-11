## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_accounts_access.html#orgs_manage_accounts_access-cross-account-role
## "The scope of access for this role includes all principals in the management account,"
## ---------------------------------------------------------------------------------------------------------------------

## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers. https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_accounts_access.html#orgs_manage_accounts_access-cross-account-role
## "The scope of access for this role includes all principals in the management account,"
## ---------------------------------------------------------------------------------------------------------------------
provider "aws" {
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}

# Cognito custom domains require the ACM cert to be in us-east-1 regardless
# of where the user pool actually lives (ours is us-west-2). Aliased provider
# scoped to us-east-1 just for that ACM resource.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}
