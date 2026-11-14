## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements. Version pinning belongs to the calling environment.
##
## The module spans two accounts. `aws` is the prod account: the trail bucket
## and the Roles Anywhere roles. `aws.management` is the management account of
## the organization: the organization trail and the audit role there.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.management]
    }
  }
}
