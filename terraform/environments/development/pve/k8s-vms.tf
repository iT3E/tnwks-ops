locals {
  pve_vms = [
    "sce-uk8s01",
    "sce-uk8s02",
    "sce-uk8s03",
  ]
}

module "pve_vm" {
  for_each             = toset(local.pve_vms)
  source               = "./modules/proxmox"
  vm_name              = each.value
  iso_image_location   = "ceph:iso/ubuntu20.1"
  ha_group             = "ha_group1"
  network_interfaces   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "10"
    },
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "20"
    },
  ]
  disks = [
    {
      storage   = "local-lvm"
      size      = "20G"
      type      = "virtio"
      cache     = "writeback"
      format    = "qcow2"
    },
    {
      storage   = "local-lvm"
      size      = "30G"
      type      = "virtio"
      cache     = "writeback"
      format    = "qcow2"
    },
  ]
}

