terraform {
  required_version = ">= 1.9"

  required_providers {
    unifi = {
      source  = "ubiquiti-community/unifi"
      version = "~> 0.65"
    }
  }
}
