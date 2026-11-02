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
  })
  sensitive = true

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
  description = "Access points by key. Each is adopted (when pending), named, addressed and has its radios pinned. ng = 2.4 GHz, na = 5 GHz; ht is the channel width in MHz."
  type = map(object({
    name = string
    mac  = string
    ip   = string
    ng   = object({ channel = string, ht = number })
    na   = object({ channel = string, ht = number })
  }))
}
