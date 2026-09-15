## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_accounts_access.html#orgs_manage_accounts_access-cross-account-role
## "The scope of access for this role includes all principals in the management account,"
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}
