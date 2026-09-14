## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements. No version constraints: the calling environment owns
## pinning so every caller resolves one consistent set.
##
## configuration_aliases declares the us-east-1 provider this module expects to
## be handed for the Cognito custom-domain certificate.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
    random = {
      source = "hashicorp/random"
    }
  }
}
