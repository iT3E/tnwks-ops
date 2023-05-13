resource "proxmox_vm_qemu" "proxmox_vms" {
  name        = var.vm_name
  iso         = var.iso_image_location
  hagroup     = var.ha_group
  #full_clone  = false
  target_node = "sce-pve-cl01"
  agent       = var.qemu_guest_agent
  vmid        = "0"
  qemu_os     = var.qemu_os
  memory      = var.vm_memory
  cores       = var.num_cores
  sockets     = var.num_sockets
  numa        = true
  hotplug     = "network,disk,usb"

  dynamic "vm_nics" {
    for_each = var.vm_nics
    content {
      model        = network_interface.value["model"]
      bridge       = network_interface.value["bridge"]
      tag          = network_interface.value["tag"]
    }
  }

  dynamic "vm_disks" {
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
