variable "cluster_name" {
    default = "homelab-talos-1"

}

variable "vm_name" {
    description = "PVE VM name"
}

variable "worker_node_count" {
    description = "Number of worker nodes for the cluster."
    type = number
    default = 5
}

variable "vm_memory" {
    description = "The amount of memory in MiB to give the VM."
    type = number
}

variable "control_plane_node_count" {
    description = "Number of control plane nodes for the cluster. Must be an odd number."
    type = number
    default = 3
    validation {
        condition = var.control_plane_node_count % 2 != 0
        error_message = "The control plane node count must be an odd number."
    }
}

variable "boot_disk_storage_pool" {
    description = "The name of the storage pool where boot disks for the cluster nodes will be stored."
    type = string
    default = "ceph_erasure_default"
}

variable "boot_disk_size" {
    description = "The size of the boot disks. A numeric string with G, M, or K appended ex: 512M or 32G."
    type = string
    default = "100G"
}

variable "config_network_bridge" {
    description = "The name of the network bridge on the Proxmox host that will be used for the configuration network."
    type = string
    default = "vmbr0"
}

variable "homelab_vlan" {
    description = "The VLAN that nodes will recieve DHCP IP assignments and be accessible to other clients on the network."
    type = number
    default = 1000
}

variable "pve_vlan" {
    description = "The VLAN that nodes will recieve DHCP IP assignments and be accessible to other clients on the network."
    type = number
    default = 4000
}

variable "wifi_vlan" {
    description = "The wifi vlan"
    type = number
    default = 1010
}

variable "network_bridge" {
    description = "The name of the network bridge on the Proxmox host."
    type = string
    default = "vmbr0"
}

variable "qemu_guest_agent" {
  default = 1
}

variable "proxmox_host_node" {
    description = "The name of the proxmox node where the cluster will be deployed"
    type = string
    default = "sce-pve01"
}

variable "proxmox_tls_insecure" {
    description = "If the TLS connection is insecure (self-signed). This is usually the case."
    type = bool
    default = true
}

variable "proxmox_debug" {
    description = "If the debug flag should be set when interacting with the Proxmox API."
    type = bool
    default = false
}

variable "ha_group" {
    description = "Sets the name of the HA Group."
}

variable "qemu_os" {
    description = "The type of OS in the guest.  Set to properly allow Proxmox to enable optimizations for the appropriate guest OS."
    validation {
        condition     = contains(["l26", "win8", "win10", "win11"], var.qemu_os)
        error_message = "The value of qemu_os must be one of: l26, win8, win10, win11."
    }
}
      # other unspecified OS
      # wxp Microsoft Windows XP
      # w2k Microsoft Windows 2000
      # w2k3 Microsoft Windows 2003
      # w2k8 Microsoft Windows 2008
      # wvista Microsoft Windows Vista
      # win7 Microsoft Windows 7
      # win8 Microsoft Windows 8/2012/2012r2
      # win10 Microsoft Windows 10/2016/2019
      # win11 Microsoft Windows 11/2022
      # l24 Linux 2.4 Kernel
      # l26 Linux 2.6 - 6.X Kernel


variable "num_cores" {
    description = "Sets the number of VM cores."
}

variable "num_sockets" {
    description = "Sets the number of VM sockets."
}

variable "vm_nics" {
  description = "The list of NIC configurations for the VM"
  type = list(object({
    model = string
    bridge = string
    tag = optional(number)
  }))
  default = [
    {
      model = "virtio"
      bridge = "vmbr0"
    }
  ]
}

variable "vm_disks" {
  description = "The list of disk configurations for the VM"
  type        = list(object({
    size        = string
    storage     = string
    format      = string
    type        = string
    cache       = string
    iothread    = number
  }))
}

variable "clone" {
  description = "VM name to base the clone from"
  default     = "ubuntu-server-focal"
  type        = string
}

variable "full_clone" {
  description = "Whether to deploy as a full clone or linked clone"
  default = "true"
}

variable "scsihw" {
  description = "The SCSI controller to emulate. Options: lsi, lsi53c810, megasas, pvscsi, virtio-scsi-pci, virtio-scsi-single."
  default     = "virtio-scsi-single"
  type        = string
}

variable "file" {
  description = "The SCSI controller to emulate. Options: lsi, lsi53c810, megasas, pvscsi, virtio-scsi-pci, virtio-scsi-single."
  type        = string
}

variable "volume" {
  description = "The SCSI controller to emulate. Options: lsi, lsi53c810, megasas, pvscsi, virtio-scsi-pci, virtio-scsi-single."
  type        = string
}

variable "backup" {
  description = "The SCSI controller to emulate. Options: lsi, lsi53c810, megasas, pvscsi, virtio-scsi-pci, virtio-scsi-single."
  default     = true
  type        = bool
}
