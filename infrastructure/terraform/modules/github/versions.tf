## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements for this module. No version constraint: the calling
## root module pins integrations/github.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_version = ">= 1.9"

  required_providers {
    github = {
      source = "integrations/github"
    }
  }
}
