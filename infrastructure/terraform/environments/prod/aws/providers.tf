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

# The management account of the organization. The workspace's own credentials
# belong to it, so this provider assumes no role. For the organization trail
# and the audit role there (modules/aws/security).
provider "aws" {
  alias = "management"
}

# Cloud Control, for the one thing the aws provider has no data source for:
# the list of trails in each account (modules/aws/security/cloudtrail.tf).
provider "awscc" {
  assume_role = {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}

provider "awscc" {
  alias = "management"
}
