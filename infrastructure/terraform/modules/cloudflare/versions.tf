## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider requirements for this module. No version constraint — the calling
## root module pins cloudflare/cloudflare.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
    }
  }
}
