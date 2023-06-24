terraform {
  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = ">= 2.9.10"
    }
  }
}

resource "proxmox_vm_qemu" "proxmox_vm" {
  name        = var.vm_name
  iso         = var.iso_image_location
  hagroup     = var.ha_group
  #full_clone  = false
  target_node = "sce-pve01"
  agent       = var.qemu_guest_agent
  qemu_os     = var.qemu_os
  memory      = var.vm_memory
  cores       = var.num_cores
  sockets     = var.num_sockets
  numa        = true
  hotplug     = "network,disk,usb"

  dynamic "network" {
    for_each = var.vm_nics
    content {
      model        = network.value.model
      bridge       = network.value.bridge
      tag          = network.value.tag != null ? network.value.tag : null
    }
  }

  dynamic "disk" {
    for_each = var.vm_disks
    content {
      storage   = disk.value["storage"]
      size      = disk.value["size"]
      type      = disk.value["type"]
      cache     = disk.value["cache"]
      format    = disk.value["format"]
    }
  }
}

