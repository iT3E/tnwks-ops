## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements. Version pinning belongs to the calling environment.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    tfe = {
      source = "hashicorp/tfe"
    }
    tls = {
      source = "hashicorp/tls"
    }
  }
}
