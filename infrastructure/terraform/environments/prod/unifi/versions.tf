## ---------------------------------------------------------------------------------------------------------------------
## VERSIONS
## Provider version constraints.
##
## ubiquiti-community/unifi is the maintained successor of paultyng/unifi (last
## release 2023, UniFi Network 7.x only). It supports UniFi Network 10.x.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  required_providers {
    unifi = {
      source  = "ubiquiti-community/unifi"
      version = "~> 0.65"
    }
    sops = {
      source  = "carlpett/sops"
      version = "1.4.1"
    }
  }
}
