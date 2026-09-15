## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.25.0"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
    }
  }
}
