# VyOS
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
source "proxmox-iso" "vyos" {

    # Proxmox Connection Settings
    proxmox_url = "${var.proxmox_api_url}"
    username = "${var.proxmox_api_token_id}"
    token = "${var.proxmox_api_token_secret}"
    # (Optional) Skip TLS Verification
    insecure_skip_tls_verify = true

    # VM General Settings
    node = "sce-pve01"
    vm_name = "VyOS"
    template_description = "VyOS Image"

    # VM OS Settings
    # (Option 1) Local ISO File
    iso_file = "cephfs:iso/vyos-1.4-rolling-202306030305-amd64.iso"
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
        disk_size = "10G"
        format = "qcow2"
        storage_pool = "ssd-pool"
        type = "virtio"
    }

    # VM CPU Settings
    cores = "2"
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
      "<enter><wait10><wait10><wait10><wait10><wait10><wait10>",
      "vyos<enter><wait>",
      "vyos<enter><wait>",
      "install image<enter><wait>",
      "<enter><wait2>",
      "<enter><wait2>",
      "<enter><wait2>",
      "Yes<enter><wait>",
      "<enter><wait10><wait10>",
      "<enter><wait>",
      "<enter><wait>",
      "vyos<enter><wait>",
      "vyos<enter><wait>",
      "<enter><wait10>",
      "reboot<enter><wait>",
      "Yes<enter><wait10><wait10><wait10><wait10><wait10><wait10><wait10>",
      "vyos<enter><wait>",
      "vyos<enter><wait>",
      "sudo useradd -m -U packerbootstrap<enter><wait>",
      "configure<enter><wait>",
      "set interfaces ethernet eth0 address dhcp<enter><wait>",
      "set system login user packerbootstrap authentication public-keys personal key 'AAAAB3NzaC1yc2EAAAADAQABAAACAQC5vW0AfFENV7suq4y8Y4NCz4HMy/xsaRiioQcxV4/xQyPTmATsKRdB/3Ug0ZMxlTHkQu6dpP/Wa6DcvqCXeSQAhXPCO4uDcmQVBFSeGpK+FTmIsUGv6BoMHy/2chORZXAITMyDF2HdaDAgjesAZAPqjmIxr460h6W6mwY3gKTiOlvUM8R4mZFbcUqru+iH0U1gnowt1uEYClBc7d95ShBWiZg8jt2qcIRztyAqT6XDO8GMnygPUoIjQdJAFboUGetzMkQPMZKOvBugfYPt3JRmN7SeuQrT+w+KYnIebKl2LsX6iZdeWVj7ZHOuwFwBFFSJH/p5DSEFi7erD86sQNdviwKHwcFVDpXeZGuP0UYKR71MQQhTzTv/7La503V3h2e2Q9l8zk5Jy6HFvizEu0YUrioA7sPj1kiTEMJAb7lJiAR92W/iLr/7AlMFvr985oTx4xZTOf9L68CdzZgJ36k16YIzHpFGQgWaz4iR96LsHKhcmQjgdYL60WZN5+Od+VX0CuV4/IruV4O/MRQ7C7zb9wGEMR9uu8BbHwnobLxffpNphahX+/9p8QZrMyQ3IToIop/c5LF9xEUr53mmuFhTcJOLOjNHKUpzDAakpD1sXYuxX0kMvlMkbYBdhBpCPnSOZKfUaJaEK4DLtV+Nybu3y1wHx8lDzuHbbGj679VpSw=='<enter><wait>",
      "set system login user packerbootstrap authentication public-keys personal type 'ssh-rsa'<enter><wait>",
      "set service ssh<enter><wait>",
      "commit<enter><wait>",
      "save<enter><wait>",
      "exit<enter><wait>",
      "exit<enter><wait5>"
      # "vagrant<enter><wait>",
      # "vagrant<enter><wait>",
      # "configure<enter><wait>",
      # "delete system login user vyos<enter><wait>",
      # "commit<enter><wait>",
      # "save<enter><wait>",
      # "exit<enter><wait>"
    ]
    boot = "c"
    boot_wait = "5s"

    # PACKER Autoinstall Settings
    #http_directory = "packer/proxmox/ubuntu-server-focal/http"
    # (Optional) Bind IP Address and Port
    #http_bind_address = "0.0.0.0"
    #http_port_min = 8802
    #http_port_max = 8802

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

    name = "vyos"
    sources = ["source.proxmox-iso.vyos"]

  # Provisioning the VM Template for Cloud-Init Integration in Proxmox #2
  ## clones personal public vyos repo
    provisioner "shell" {
        inline = [
          "git init",
          "git config --global init.defaultBranch main",
          "git remote add origin git@github.com:iT3E/vyos-config.git",
          "git branch --set-upstream-to=origin/main main", # not 100% certain this is required
          "git checkout main -f",
          "git log"
        ]
    }
  # Provisioning the VM Template for Integration in Proxmox #2
    provisioner "shell" {
        inline = [
            "set -e",
            "set -x",
            "sudo rm /etc/ssh/ssh_host_*",
            "sudo truncate -s 0 /etc/machine-id",
            "sudo apt -y autoremove --purge",
            "sudo apt -y clean",
            "sudo cloud-init clean",
            "WRAPPER=/opt/vyatta/sbin/vyatta-cfg-cmd-wrapper",
            "$WRAPPER begin",
            "$WRAPPER delete interfaces ethernet eth0 hw-id",
            "$WRAPPER commit",
            "$WRAPPER save",
            "$WRAPPER end",
            "sudo sync"
        ]
    }


    # Provisioning the VM Template for Cloud-Init Integration in Proxmox #3
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
