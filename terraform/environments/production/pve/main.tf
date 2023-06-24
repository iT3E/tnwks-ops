terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    #look for PII leak here
    workspaces {
      name = "tnwks-pve-prod"
    }
  }
    required_version = ">= 1.2.2"
   required_providers {
     proxmox = {
       source = "Telmate/proxmox"
       version = "= 2.9.14"
   }
     sops = {
       source  = "carlpett/sops"
       version = "0.7.2"
   }
 }
}

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

######################
######################



#######################################
##                                   ##
##              Locals               ##
##                                   ##
#######################################


locals {
  hass = [
    "sce-hass01"
  ]
  k8s_masters = [
    "sce-uk8sm01",
    "sce-uk8sm02",
    "sce-uk8sm03",
  ]
  k8s_workers = [
    "sce-uk8sw01",
    "sce-uk8sw02",
    "sce-uk8sw03",
  ]
  uisp = [
    "sce-uisp01"
  ]
  vyos = [
    "sce-vyos01",
    "sce-vyos02"
  ]
  ad = [
    "sce-dc01",
    "sce-dc02"
  ]
  biris = [
    "sce-biris01"
  ]
}

#######################################
##                                   ##
##        PVE VM - sce-hass01        ##
##                                   ##
#######################################

module "pve_vm_hass" {
  for_each             = toset(local.hass)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.hass, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "8192"
  num_cores            = "2"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "720"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "20G"
      type      = "virtio"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

#######################################
##                                   ##
##        PVE VM - k8s_masters       ##
##                                   ##
#######################################

module "pve_vm_k8s_masters" {
  for_each             = toset(local.k8s_masters)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.k8s_masters, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "8192"
  num_cores            = "2"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "120"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "20G"
      type      = "virtio"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}


#######################################
##                                   ##
##        PVE VM - k8s_workers       ##
##                                   ##
#######################################


module "pve_vm_k8s_workers" {
  for_each             = toset(local.k8s_workers)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.k8s_workers, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "24576"
  num_cores            = "16"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent                = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "120"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "20G"
      type      = "virtio"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

#######################################
##                                   ##
##          PVE VM - UISP            ##
##                                   ##
#######################################

module "pve_vm_uisp" {
  for_each             = toset(local.uisp)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.uisp, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "2048"
  num_cores            = "2"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "720"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "20G"
      type      = "virtio"
      cache     = "default"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

#######################################
##                                   ##
##          PVE VM - vyos            ##
##                                   ##
#######################################

module "pve_vm_vyos" {
  for_each             = toset(local.vyos)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "VyOS"
  full_clone           = true
  ha_group             = "ha_group${index(local.vyos, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "4096"
  num_cores            = "2"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "20G"
      type      = "scsi"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

#######################################
##                                   ##
##         PVE VM - win-ad           ##
##                                   ##
#######################################

module "pve_vm_ad" {
  for_each             = toset(local.ad)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.ad, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "2048"
  num_cores            = "1"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "110"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "40G"
      type      = "virtio"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

#######################################
##                                   ##
##        PVE VM - win-biris         ##
##                                   ##
#######################################

module "pve_vm_biris" {
  for_each             = toset(local.biris)
  source               = "../../../modules/proxmox"
  vm_name              = each.value
  clone                = "ubuntu-server-focal"
  full_clone           = true
  ha_group             = "ha_group${index(local.biris, each.value) + 1}" #ha groups must be pre-created in pve, with correct naming scheme, along with each having 'priorities' mapped to individual physical hosts
  vm_memory            = "8192"
  num_cores            = "4"
  num_sockets          = "2"
  qemu_os              = "l26"
  qemu_guest_agent     = 1
  vm_nics   = [
    {
      model        = "virtio"
      bridge       = "vmbr0"
      tag          = "610"
    },
  ]
  vm_disks = [
    {
      storage   = "ssd-pool"
      size      = "40G"
      type      = "virtio"
      cache     = "none"
      format    = "qcow2"
      iothread  = 1
      file       = "vm-${each.value}-disk-0"
      volume    = "ssd-pool:vm-${each.value}-disk-0"
      backup    = true
    },
  ]
}

