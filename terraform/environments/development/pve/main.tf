terraform {
  required_providers {
    proxmox = {
      source = "Telmate/proxmox"
      version = ">= 2.9.10"
    }
  }
}

locals {
  pve_vms = [
    "sce-ubu01",
    "sce-ubu02",
    "sce-ubu03",
  ]
}

module "pve_vm" {
  for_each         = toset(local.pve_cms)
  source           = "./modules/proxmox"
}
