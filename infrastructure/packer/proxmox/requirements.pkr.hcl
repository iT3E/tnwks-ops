packer {
  required_plugins {
    proxmox-iso = {
      version = ">= v1.1.3"
      source = "github.com/hashicorp/proxmox"
    }
  }
}
