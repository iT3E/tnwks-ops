locals {
  pve_vms = [
    "sce-uk8s01",
    "sce-uk8s02",
    "sce-uk8s03",
  ]
}

module "pve_vm" {
  for_each         = toset(local.pve_vms)
  source           = "./modules/proxmox"
  hagroup          = each.key
}
