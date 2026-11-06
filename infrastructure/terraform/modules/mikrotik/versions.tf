terraform {
  required_version = ">= 1.9"

  required_providers {
    routeros = {
      source  = "terraform-routeros/routeros"
      version = "~> 1.99"
    }
    # Only for /routing/bgp/connection (bgp.tf): routeros v1.99.1 always sends
    # add-path-out, which RouterOS 7.22+ removed, so it can't write one.
    restapi = {
      source  = "Mastercard/restapi"
      version = "~> 3.0"
    }
  }
}
