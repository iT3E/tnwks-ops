## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    routeros = {
      source  = "terraform-routeros/routeros"
      version = "~> 1.99"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
    }
  }
}
