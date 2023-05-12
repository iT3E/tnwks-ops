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
    network {
        model  = "virtio"
        bridge = var.network_bridge
        tag    = var.homelab_vlan
        macaddr = "4a:10:50:00:00:00"
    }
    network {
        model  = "virtio"
        bridge = var.network_bridge
        tag    = var.pve_vlan
        macaddr = "4a:20:50:00:00:00"
    }
    disk {
        type    = "virtio"
        size    = var.boot_disk_size
        storage = var.boot_disk_storage_pool
    }
}
