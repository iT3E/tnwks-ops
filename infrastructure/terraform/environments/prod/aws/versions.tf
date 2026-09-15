## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints. Kept separate from backend.tf so provider
## bumps never touch backend configuration.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "~> 1.4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
