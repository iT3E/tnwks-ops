## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements for this module. No version constraint: the calling
## root module pins kristofferahl/healthchecksio.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_version = ">= 1.9"

  required_providers {
    healthchecksio = {
      source = "kristofferahl/healthchecksio"
    }
  }
}
