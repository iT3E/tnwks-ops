## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Thin root module: modules/unifi holds the resources, this file the values,
## and secrets.sops.yaml everything that must stay out of a public repo.
##
## The values reproduce the pre-2026 setup as observed from outside the APs on
## 2026-11-01, after their old controller's database was lost. Where each value
## came from: docs/network/unifi.md.
##
## The SSID and the AP MAC addresses live in SOPS: an SSID or a BSSID (UniFi
## derives those from the MAC) is enough to find the house on a wardriving map.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  secrets = data.sops_file.secrets.data

  # Same key the router's admin account trusts (environments/prod/mikrotik).
  desktop_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF6lX74KwH2/4FQHwIwKUHhsP9CxnvliHLKxcVPTLD2B"

  # Static addresses on unifi-mgmt-900, outside the router's DHCP pool
  # (10.10.90.100-200) so a freshly reset AP on a lease can't collide with one.
  # The old controller had them on .100-.102, inside the pool.
  #
  # Radios as observed on air, except Master's 2.4 GHz, which moved from
  # channel 1 to 11: it shared channel 1 with Kitchen. 5 GHz widths: Kitchen
  # measured at 40 MHz, the other two assumed to match.
  #
  # Rooms: the pre-2026 notes had Living Room and Master swapped. The UAP-HD on
  # SW03 GE0/0/12 is in the living room; the AC-Pro on GE0/0/1 is in the master
  # bedroom (confirmed on site 2026-11-01).
  #
  # managed = false keeps an AP out of Terraform until it is adopted, so plans
  # stay clean (CI applies on merge). Living Room: its reset button broke, so
  # it still runs standalone on the old controller's config (same SSID and
  # passphrase, channels 6/149) until it can be reset.
  all_access_points = {
    kitchen = {
      name    = "AP Kitchen" # SW03 GE0/0/0, UAP-AC-Pro-Gen2
      mac     = local.secrets["ap_kitchen_mac"]
      ip      = "10.10.90.11"
      ng      = { channel = "1", ht = 20 }
      na      = { channel = "40", ht = 40 }
      managed = true
    }
    master = {
      name    = "AP Master" # SW03 GE0/0/1, UAP-AC-Pro-Gen2
      mac     = local.secrets["ap_master_mac"]
      ip      = "10.10.90.12"
      ng      = { channel = "11", ht = 20 }
      na      = { channel = "44", ht = 40 }
      managed = true
    }
    living_room = {
      name    = "AP Living Room" # SW03 GE0/0/12, UAP-HD
      mac     = local.secrets["ap_living_room_mac"]
      ip      = "10.10.90.13"
      ng      = { channel = "6", ht = 20 }
      na      = { channel = "149", ht = 40 }
      managed = false
    }
  }

  access_points = {
    for k, ap in local.all_access_points : k => {
      name = ap.name
      mac  = ap.mac
      ip   = ap.ip
      ng   = ap.ng
      na   = ap.na
    } if ap.managed
  }
}

# The AP adopted first as "living_room" is the master-bedroom AP.
moved {
  from = module.unifi.unifi_device.ap["living_room"]
  to   = module.unifi.unifi_device.ap["master"]
}

module "unifi" {
  source = "../../../modules/unifi"

  # 6.6.77 is the last firmware for the UAP-AC-Pro-Gen2 and UAP-HD.
  auto_upgrade = false

  device_ssh = {
    username = local.secrets["device_ssh_username"]
    password = local.secrets["device_ssh_password"]
    keys = [
      {
        name    = "it-pc01-wsl"
        type    = split(" ", local.desktop_ssh_public_key)[0]
        key     = split(" ", local.desktop_ssh_public_key)[1]
        comment = "WSL on the desktop"
      },
    ]
  }

  client_network = {
    name = "unifi-frontend-910"
    vlan = 910
  }

  wlan = {
    ssid       = local.secrets["wlan_ssid"]
    passphrase = local.secrets["wlan_passphrase"]
  }

  management = {
    netmask   = "255.255.255.0"
    gateway   = "10.10.90.1"
    dns       = "10.10.90.1"
    dnssuffix = "tnwks.local"
  }

  access_points = local.access_points
}
