## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "6.13.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
    }
  }
}
