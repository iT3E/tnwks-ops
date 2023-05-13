locals {
  pve_vms = [
    "sce-opns01"
  ]
}

module "pve_vm" {
  for_each             = toset(local.pve_vms)
  source               = "./modules/proxmox"
  vm_name              = each.value
  iso_image_location   = "ceph:iso/ubuntu20.1"
  ha_group             = "ha_group${index(local.pve_vms, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  memory               = "8192"
  cores                = "4"
  sockets              = "2"
  qemu_os              = "l26"
  agent                = 1
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

