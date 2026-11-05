## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## ---------------------------------------------------------------------------------------------------------------------

variable "country_code" {
  description = "Regulatory country, ISO 3166-1 numeric (840 = United States)."
  type        = number
  default     = 840
}

variable "auto_upgrade" {
  description = "Let the controller upgrade device firmware on its own."
  type        = bool
  default     = false
}

variable "auto_upgrade_hour" {
  description = "Hour of day (0-23, controller time zone) for automatic firmware upgrades."
  type        = number
  default     = 3
}

variable "device_ssh" {
  description = "SSH credentials the controller pushes to every adopted device, plus authorized public keys (type + base64 body, no comment)."
  type = object({
    username = string
    password = string
    keys = optional(list(object({
      name    = string
      type    = string
      key     = string
      comment = optional(string)
    })), [])
  })
  sensitive = true
}

variable "client_network" {
  description = "The VLAN the WLAN bridges clients onto. Routing and DHCP stay on the router (a third-party-gateway network)."
  type = object({
    name = string
    vlan = number
  })
}

variable "wlan" {
  description = "The one SSID: name, WPA2 passphrase and bands."
  type = object({
    ssid       = string
    passphrase = string
    bands      = optional(list(string), ["2g", "5g"])
    # 802.11v BSS transition: lets the AP point a client at a better AP.
    # Clients that don't support it ignore it, unlike 802.11r.
    bss_transition = optional(bool, true)
    # 5 GHz Roaming Assistant: the AP disconnects a 5 GHz client whose signal
    # drops below rssi (dBm, -80 to -60) so it rejoins a closer AP. Null leaves
    # the controller's setting alone.
    roaming_assistant_5g_rssi = optional(number)
  })
  sensitive = true

  validation {
    condition     = var.wlan.roaming_assistant_5g_rssi == null ? true : (var.wlan.roaming_assistant_5g_rssi >= -80 && var.wlan.roaming_assistant_5g_rssi <= -60)
    error_message = "roaming_assistant_5g_rssi must be between -80 and -60 dBm."
  }

  validation {
    condition     = length(var.wlan.passphrase) >= 8 && var.wlan.passphrase != "CHANGEME"
    error_message = "Set the WLAN passphrase to the current Wi-Fi password. Applying a different one disconnects every client."
  }
}

variable "management" {
  description = "Static addressing for the APs on their management VLAN."
  type = object({
    netmask   = string
    gateway   = string
    dns       = string
    dnssuffix = optional(string)
  })
}

variable "access_points" {
  description = "Access points by key. Each is adopted (when pending), named, addressed and has its radios pinned. ng = 2.4 GHz, na = 5 GHz; ht is the channel width in MHz. tx_power_mode (auto, low, medium, high, custom) and tx_power (dBm, with custom) are optional; left out, the controller keeps its own."
  type = map(object({
    name = string
    mac  = string
    ip   = string
    ng = object({
      channel       = string
      ht            = number
      tx_power_mode = optional(string)
      tx_power      = optional(string)
    })
    na = object({
      channel       = string
      ht            = number
      tx_power_mode = optional(string)
      tx_power      = optional(string)
    })
  }))

  validation {
    condition = alltrue(flatten([
      for ap in values(var.access_points) : [
        for r in [ap.ng, ap.na] : r.tx_power_mode == null ? true : contains(["auto", "low", "medium", "high", "custom"], r.tx_power_mode)
      ]
    ]))
    error_message = "tx_power_mode must be one of auto, low, medium, high, custom."
  }

  validation {
    condition = alltrue(flatten([
      for ap in values(var.access_points) : [
        for r in [ap.ng, ap.na] : (r.tx_power == null) == (r.tx_power_mode != "custom")
      ]
    ]))
    error_message = "Set tx_power exactly when tx_power_mode is custom."
  }
}
