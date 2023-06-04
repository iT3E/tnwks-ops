# Ubuntu Server Focal
# ---
# Packer Template to create an Ubuntu Server (Focal) on Proxmox

# Variable Definitions
variable "proxmox_api_url" {
  type    = string
  default = "${env("PROXMOX_API_URL")}"
}

variable "proxmox_api_token_id" {
  type    = string
  default = "${env("PROXMOX_API_TOKEN_ID")}"
}

variable "proxmox_api_token_secret" {
  type      = string
  default   = "${env("PROXMOX_API_TOKEN_SECRET")}"
  sensitive = true
}

variable "ssh_secondary_username" {
  type      = string
  default   = "${env("SSH_SECONDARY_USERNAME")}"
  sensitive = true
}

variable "ssh_secondary_pub" {
  type      = string
  default   = "${env("SSH_SECONDARY_PUB")}"
  sensitive = true
}

# Resource Definiation for the VM Template
source "proxmox-iso" "ubuntu-server-focal" {

    # Proxmox Connection Settings
    proxmox_url = "${var.proxmox_api_url}"
    username = "${var.proxmox_api_token_id}"
    token = "${var.proxmox_api_token_secret}"
    # (Optional) Skip TLS Verification
    insecure_skip_tls_verify = true

    # VM General Settings
    node = "sce-pve01"
    vm_name = "ubuntu-server-focal"
    template_description = "Ubuntu Server Focal Image"

    # VM OS Settings
    # (Option 1) Local ISO File
    iso_file = "cephfs:iso/ubuntu-20.04.6-live-server-amd64.iso"
    # - or -
    # (Option 2) Download ISO
    ####
    #Option 2 is bugged https://forum.proxmox.com/threads/pveproxy-no-space-left-on-device-problem-with-storage-api.113451/
    #I applied fixes listed here, but still received 'no space left on device' error.  uploading iso via GUI for now..
    #####
    #iso_url = "https://releases.ubuntu.com/20.04/ubuntu-20.04.6-live-server-amd64.iso"
    #iso_checksum = "b8f31413336b9393ad5d8ef0282717b2ab19f007df2e9ed5196c13d8f9153c8b"
    #iso_storage_pool = "cephfs"
    unmount_iso = true

    # VM System Settings
    qemu_agent = true

    # VM Hard Disk Settings
    scsi_controller = "virtio-scsi-pci"

    disks {
        disk_size = "20G"
        format = "qcow2"
        storage_pool = "ssd-pool"
        type = "virtio"
    }

    # VM CPU Settings
    cores = "1"
    sockets = "2"

    # VM Memory Settings
    memory = "4096"

    # VM Network Settings
    network_adapters {
        model = "virtio"
        bridge = "vmbr0"
        firewall = "false"
        vlan_tag = "910"
    }

    # VM Cloud-Init Settings
    cloud_init = true
    cloud_init_storage_pool = "ssd-pool"

    # PACKER Boot Commands
    boot_command = [
        "<esc><wait><esc><wait>",
        "<f6><wait><esc><wait>",
        "<bs><bs><bs><bs><bs>",
        "autoinstall ds=nocloud-net;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/ ",
        "--- <enter>"
    ]
    boot = "c"
    boot_wait = "5s"

    # PACKER Autoinstall Settings
    http_directory = "packer/proxmox/ubuntu-server-focal/http"
    # (Optional) Bind IP Address and Port
    http_bind_address = "0.0.0.0"
    http_port_min = 8802
    http_port_max = 8802

    ssh_username = "packerbootstrap"

    # (Option 1) Add your Password here
    #ssh_password = "${var.packer_ssh_password}"
    # - or -
    # (Option 2) Add your Private SSH KEY file here
    ssh_private_key_file = "~/.ssh/packer_rsa"

    # Raise the timeout, when installation takes longer
    ssh_timeout = "20m"
}

# Build Definition to create the VM Template
build {

    name = "ubuntu-server-focal"
    sources = ["source.proxmox-iso.ubuntu-server-focal"]

    # Provisioning the VM Template for Cloud-Init Integration in Proxmox #1
    provisioner "shell" {
        inline = [
            "while [ ! -f /var/lib/cloud/instance/boot-finished ]; do echo 'Waiting for cloud-init...'; sleep 1; done",
            "sudo rm /etc/ssh/ssh_host_*",
            "sudo truncate -s 0 /etc/machine-id",
            "sudo apt -y autoremove --purge",
            "sudo apt -y clean",
            "sudo apt -y autoclean",
            "sudo cloud-init clean",
            "sudo rm -f /etc/cloud/cloud.cfg.d/subiquity-disable-cloudinit-networking.cfg",
            "sudo sync"
        ]
    }

    # Provisioning the VM Template for Cloud-Init Integration in Proxmox #2
    provisioner "file" {
        source = "packer/proxmox/ubuntu-server-focal/files/99-pve.cfg"
        destination = "/tmp/99-pve.cfg"
    }

    # Provisioning the VM Template for Cloud-Init Integration in Proxmox #3
    provisioner "shell" {
        inline = [ "sudo cp /tmp/99-pve.cfg /etc/cloud/cloud.cfg.d/99-pve.cfg" ]
    }

    # Provisioning the VM Template for Cloud-Init Integration in Proxmox #4
    provisioner "shell" {
      inline = [
        # ...
        # Create a new user and add the user's public key to their authorized_keys file
        "sudo adduser --disabled-password --gecos '' ${var.ssh_secondary_username}",
        "sudo mkdir /home/${var.ssh_secondary_username}/.ssh",
        "echo '${var.ssh_secondary_pub}' | sudo tee /home/${var.ssh_secondary_username}/.ssh/authorized_keys",
        "sudo chown -R ${var.ssh_secondary_username}:${var.ssh_secondary_username} /home/${var.ssh_secondary_username}/.ssh",
        "sudo chmod 700 /home/${var.ssh_secondary_username}/.ssh",
        "sudo chmod 600 /home/${var.ssh_secondary_username}/.ssh/authorized_keys",
        # Add the new user to the sudoers file
        "echo '${var.ssh_secondary_username} ALL=(ALL:ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${var.ssh_secondary_username}",
        # Remove the 'packerbootstrap' user
        "cd /",
        "echo 'sleep 60; pkill -u packerbootstrap; sleep 5; deluser --remove-home packerbootstrap' | at now",
      ]
    }

    # Add additional provisioning scripts here
    # ...
}
