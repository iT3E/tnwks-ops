## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements. Version pinning belongs to the calling environment.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    tls = {
      source = "hashicorp/tls"
    }
  }
}
