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
}

variable "ssh_secondary_vyospub" {
  type      = string
  default   = "${env("SSH_SECONDARY_VYOSPUB")}"
  sensitive = true
}

variable "age_private_key" {
  type      = string
  default   = "${env("AGE_PRIVATE_KEY")}"
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
    #template_description = "VyOS Image"

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
    scsi_controller = "virtio-scsi-single"

    disks {
        disk_size = "10G"
        format = "qcow2"
        storage_pool = "ssd-pool"
        type = "virtio"
        io_thread  = true
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
    cloud_init = false
    #cloud_init_storage_pool = "ssd-pool"

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
      "configure<enter><wait>",
      "set interfaces ethernet eth0 address dhcp<enter><wait>",
      "set system name-server 8.8.8.8<enter><wait>",
      # below two commands are inconsistent.  It may require manually connecting with vyos+vyos and running these commands before provisioners kick off.
      # the problem seems to be with the amount of charaters being entered, or possibly the end characters causing issues with <enter><wait>.
      # 2023-06-25: latest problem was there being a character amount mismatch between what is listed here and what was in authorized_keys.  Other times
      # the problem has been doing to capitalization.
      "set system login user packerbootstrap authentication public-keys personal key AAAAB3NzaC1yc2EAAAADAQABAAACAQC5vW0AfFENV7suq4y8Y4NCz4HMy/xsaRiioQcxV4/xQyPTmATsKRdB/3Ug0ZMxlTHkQu6dpP/Wa6DcvqCXeSQAhXPCO4uDcmQVBFSeGpK+FTmIsUGv6BoMHy/2chORZXAITMyDF2HdaDAgjesAZAPqjmIxr460h6W6mwY3gKTiOlvUM8R4mZFbcUqru+iH0U1gnowt1uEYClBc7d95ShBWiZg8jt2qcIRztyAqT6XDO8GMnygPUoIjQdJAFboUGetzMkQPMZKOvBugfYPt3JRmN7SeuQrT+w+KYnIebKl2LsX6iZdeWVj7ZHOuwFwBFFSJH/p5DSEFi7erD86sQNdviwKHwcFVDpXeZGuP0UYKR71MQQhTzTv/7La503V3h2e2Q9l8zk5Jy6HFvizEu0YUrioA7sPj1kiTEMJAb7lJiAR92W/iLr/7AlMFvr985oTx4xZTOf9L68CdzZgJ36k16YIzHpFGQgWaz4iR96LsHKhcmQjgdYL60WZN5+Od+VX0CuV4/IruV4O/MRQ7C7zb9wGEMR9uu8BbHwnobLxffpNphahX+/9p8QZrMyQ3IToIop/c5LF9xEUr53mmuFhTcJOLOjNHKUpzDAakpD1sXYuxX0kMvlMkbYBdhBpCPnSOZKfUaJaEK4DLtV+Nybu3y1wHx8lDzuHbbGj679VpSw==<enter><wait5>",
      "set system login user packerbootstrap authentication public-keys personal type ssh-rsa<enter><wait>",
      "set service ssh<enter><wait>",
      "commit<enter><wait5>",
      "save<enter><wait>",
      "exit<enter><wait>",
      "exit<enter><wait>"
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
  ## clones personal vyos-config repo
    provisioner "shell" {
        inline = [
          #"sudo mkdir -p /opt/vyos-config",
          #"sudo chown packerbootstrap /opt/vyos-config",
          #"cd /opt/vyos-config",
          "cd /config",
          "git config --global init.defaultBranch main",
          "git config --global --add safe.directory /config",
          "git init",
          "git remote add origin https://github.com/iT3E/vyos-config.git",
          "git fetch origin",
          "git checkout main -f",
          "git log",
          "sudo rm /etc/ssh/ssh_host_*",
          "sudo truncate -s 0 /etc/machine-id",
          "sudo apt -y autoremove --purge",
          "sudo apt -y clean",
          #install age
          "curl -Lo age.tar.gz 'https://github.com/FiloSottile/age/releases/download/v1.1.1/age-v1.1.1-linux-amd64.tar.gz'",
          "tar xf age.tar.gz",
          "sudo mv age/age /usr/local/bin",
          "sudo mv age/age-keygen /usr/local/bin",
          "rm -rf age.tar.gz",
          "rm -rf age",
          #configure age
          "sudo mkdir -p /config/secrets",
          "echo '# created: 2023-06-06T12:37:37-07:00' | sudo tee /config/secrets/age.key",
          "echo '# public key: age1mzxwncaj0364q8jxfj0y73ptt63czwxucf9lrnn28c7kwyr5wfdsj9gsk2' | sudo tee -a /config/secrets/age.key",
          "echo '${var.age_private_key}' | sudo tee -a /config/secrets/age.key"
        ]
    }

  # Provisioning the VM Template for Integration in Proxmox #2
    provisioner "shell" {
        inline_shebang = "/bin/vbash"
        inline = [
          "source /opt/vyatta/etc/functions/script-template",
          "configure",
          "set system login user ${var.ssh_secondary_username} authentication public-keys personal key ${var.ssh_secondary_vyospub}",
          "set system login user ${var.ssh_secondary_username} authentication public-keys personal type ssh-rsa",
          "delete system login user vyos",
          "commit",
          "save",
          "exit"
        ]
    }

    # Add additional provisioning scripts here
    # ...
}
