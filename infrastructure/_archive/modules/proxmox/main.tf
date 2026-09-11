terraform {
  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = "= 2.9.14"
    }
  }
}

resource "proxmox_vm_qemu" "proxmox_vm" {
  name        = var.vm_name
  hagroup     = var.ha_group
  hastate     = var.ha_state
  clone       = var.clone
  full_clone  = var.full_clone
  target_node = var.target_node
  agent       = var.qemu_guest_agent
  qemu_os     = var.qemu_os
  memory      = var.vm_memory
  cores       = var.num_cores
  sockets     = var.num_sockets
  scsihw      = var.scsihw
  numa        = true
  hotplug     = "network,disk,usb"
  ipconfig0 = var.primary_nic_ip != null ? "ip=${var.primary_nic_ip}" : null
  ipconfig1 = var.secondary_nic_ip != null ? "ip=${var.secondary_nic_ip}" : null

  dynamic "network" {
    for_each = var.vm_nics
    content {
      model        = network.value.model
      bridge       = network.value.bridge
      tag          = network.value.tag != null ? network.value.tag : null
      mtu          = network.value.mtu
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
      iothread  = disk.value["iothread"]
    }
  }
}

