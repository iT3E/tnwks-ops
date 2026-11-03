## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    healthchecksio = {
      source  = "kristofferahl/healthchecksio"
      version = "~> 2.3"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
    }
  }
}
