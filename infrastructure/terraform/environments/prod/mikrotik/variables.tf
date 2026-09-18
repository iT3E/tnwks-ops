## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## Declarations only. Values live in terraform.tfvars (non-secret) and
## secrets.sops.yaml (secret).
## ---------------------------------------------------------------------------------------------------------------------

variable "identity" {
  type = string
}

variable "domain" {
  type = string
}

variable "timezone" {
  type = string
}

variable "wan_interface" {
  type = string
}

variable "lan_trunk_interface" {
  type = string
}

variable "bridge_name" {
  type    = string
  default = "bridge-lan"
}

variable "bridge_ports" {
  type    = list(string)
  default = []
}

variable "vlans" {
  type = map(object({
    vlan_id = number
    address = string
    comment = optional(string)
    enabled = optional(bool, true)
    dhcp = optional(object({
      pool_start    = string
      pool_end      = string
      lease_time    = optional(string, "1d")
      dns_servers   = optional(list(string))
      ntp_servers   = optional(list(string))
      authoritative = optional(string, "yes")
      leases = optional(map(object({
        address     = string
        mac_address = string
        comment     = optional(string)
      })), {})
    }))
  }))
  default = {}
}

variable "static_routes" {
  type = map(object({
    dst_address = string
    gateway     = string
    comment     = optional(string)
    distance    = optional(number)
  }))
  default = {}
}

variable "address_lists" {
  type    = map(map(string))
  default = {}
}

variable "port_lists" {
  type    = map(list(string))
  default = {}
}

variable "zone_policies" {
  type = map(object({
    from        = string
    to          = string
    comment     = optional(string)
    log_default = optional(bool, true)
    rules = optional(list(object({
      comment          = string
      protocol         = optional(string)
      src_address_list = optional(string)
      dst_address_list = optional(string)
      dst_port         = optional(string)
      dst_port_list    = optional(string)
      src_address      = optional(string)
      dst_address      = optional(string)
    })), [])
  }))
  default = {}
}

variable "input_rules" {
  description = <<-EOT
    Explicit RouterOS `input` chain accepts. No VyOS counterpart: VyOS had no
    local zone so traffic TO the router was unfiltered. Also where the ported
    client-DNS rules land, since the resolver is now the router itself.
  EOT
  type = map(object({
    comment           = string
    protocol          = optional(string)
    dst_port          = optional(string)
    dst_port_list     = optional(string)
    in_interface_list = optional(string)
    src_address_list  = optional(string)
    src_address       = optional(string)
    icmp_options      = optional(string)
  }))
  default = {}
}

variable "input_drop_log" {
  type    = bool
  default = true
}

variable "dstnat_rules" {
  type = map(object({
    comment      = string
    in_interface = string
    protocol     = string
    dst_port     = string
    dst_address  = optional(string)
    src_address  = optional(string)
    to_address   = string
    to_port      = string
  }))
  default = {}
}

variable "masquerade_out_interface" {
  type    = string
  default = null
}

variable "dns" {
  type = object({
    upstream_servers = list(string)
    allow_remote     = optional(bool, true)
    cache_size       = optional(number, 10240)
    cache_max_ttl    = optional(string, "1d")
    use_doh_server   = optional(string)
    verify_doh_cert  = optional(bool, true)
    static_records = optional(map(object({
      name            = string
      address         = optional(string)
      cname           = optional(string)
      type            = optional(string, "A")
      match_subdomain = optional(bool, false)
      ttl             = optional(string, "1h")
      comment         = optional(string)
    })), {})
    adlists = optional(list(string), [])
  })
}

variable "ntp" {
  type = object({
    servers     = list(string)
    server_mode = optional(bool, true)
    client_mode = optional(string, "unicast")
  })
}

variable "syslog" {
  type = object({
    remote          = string
    remote_port     = optional(number, 6001)
    remote_protocol = optional(string, "tcp")
    src_address     = optional(string)
    topics          = optional(list(string), ["info", "error", "warning", "critical"])
  })
  default = null
}

variable "ssh_port" {
  type    = number
  default = 22
}

variable "disabled_ip_services" {
  type = map(number)
  default = {
    telnet  = 23
    ftp     = 21
    www     = 80
    api     = 8728
    api-ssl = 8729
  }
}

variable "wireguard_interfaces" {
  description = <<-EOT
    WireGuard listeners WITHOUT private keys. main.tf merges each key in from
    secrets.sops.yaml under `wireguard_<name>_private_key`.
  EOT
  type = map(object({
    address     = string
    listen_port = number
    comment     = optional(string)
    mtu         = optional(string)
    peers = map(object({
      public_key           = string
      allowed_address      = list(string)
      persistent_keepalive = optional(string, "15s")
      comment              = optional(string)
    }))
  }))
  default = {}
}

variable "admin_users" {
  type = map(object({
    group    = optional(string, "full")
    comment  = optional(string)
    ssh_keys = optional(list(string), [])
    address  = optional(string)
  }))
  default = {}
}
