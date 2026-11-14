## ---------------------------------------------------------------------------------------------------------------------
## DATA
## The two accounts, their Region, and the ID of the organization.
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_caller_identity" "management" {
  provider = aws.management
}

data "aws_region" "management" {
  provider = aws.management
}

# Only the organization itself (its ID is a part of the trail's key prefix):
# the list of accounts stays out of the state.
data "aws_organizations_organization" "current" {
  provider = aws.management

  return_organization_only = true
}
