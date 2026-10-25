variable "identity" {
  description = "RouterOS system identity (hostname)."
  type        = string
}

variable "domain" {
  description = "Internal DNS domain handed out by DHCP."
  type        = string
}

variable "timezone" {
  description = "IANA timezone name for RouterOS clock."
  type        = string
}

variable "wan_interface" {
  description = "Physical WAN port (cable modem). Kept out of the LAN bridge."
  type        = string
}

variable "lan_trunk_interface" {
  description = <<-EOT
    Interface carrying the tagged VLAN trunk to the Aruba switch. On the RB5009
    this is the bridge name when VLAN filtering is done on the bridge, or a
    physical port name when VLANs are terminated directly on the port.
  EOT
  type        = string
}

variable "bridge_name" {
  description = "Name of the VLAN-filtering bridge that owns the LAN trunk."
  type        = string
  default     = "bridge-lan"
}

variable "bridge_ports" {
  description = "Physical ports enslaved to the LAN bridge (tagged trunk members)."
  type        = list(string)
  default     = []
}

variable "mgmt" {
  description = <<-EOT
    Out-of-band management port, kept OUT of the LAN bridge.

    The bootstrap script puts the REST API address on this port, and Terraform
    reaches the router through it. If the port were a bridge member its address
    would go invalid the moment the bridge is created, which is a guaranteed
    lockout on the first apply. Input-chain access (SSH, REST, Winbox) is always
    accepted on this interface, so a firewall mistake elsewhere still leaves a
    cable you can plug into.

    The address itself stays owned by the bootstrap script, not Terraform: it is
    the connection Terraform applies over, so Terraform must never be able to
    destroy or replace it. `address` is recorded here for documentation and the
    bridge-membership check only.
  EOT
  type = object({
    interface = string
    address   = string
  })
  default = null
}

variable "native_vlan" {
  description = <<-EOT
    Untagged (native) network on the trunk uplink, routed on the bridge itself.

    The Aruba's own management address lives on the trunk's native VLAN, so
    untagged frames on the uplink must reach an SVI or switch management is cut
    off. Only the listed ports carry it; every other bridge port drops untagged
    frames into an unused pvid. null keeps the trunk all-tagged.
  EOT
  type = object({
    zone    = string       # zone name used by zone_policies / input_rules
    address = string       # router address on the native network, CIDR
    ports   = list(string) # bridge ports that carry it untagged (the uplink)
    comment = optional(string)
  })
  default = null
}

variable "outside_zone" {
  description = <<-EOT
    Zone that the WAN port joins, so internet traffic is governed by the same
    zone-pair rules VyOS used for its upstream.

    VyOS reached the internet through `transit-10` (the EdgeRouter hop), so every
    `X -> transit-10` ruleset was really an internet-egress policy. Adding the
    WAN port to that zone's interface list keeps those rules meaningful both
    while the EdgeRouter is still upstream and after the modem moves to the WAN
    port. null leaves the WAN port in no zone, which the terminal forward drop
    then blocks entirely.
  EOT
  type        = string
  default     = null
}

variable "upstream_gateway" {
  description = <<-EOT
    Static default route used while an upstream router is still in front of this
    one (the EdgeRouter Lite at 172.16.1.1 during the staged cutover).

    Installed at distance 10, so the WAN DHCP lease's default route (distance 1)
    wins automatically the moment the modem is plugged into the WAN port; moving
    the cable needs no Terraform change. Set to null once the EdgeRouter is gone.
  EOT
  type        = string
  default     = null
}

variable "vlans" {
  description = <<-EOT
    Routed VLANs. Keyed by VyOS zone name so firewall rules stay readable.

    vlan_id  - 802.1q tag
    address  - router SVI address in CIDR form
    comment  - human label carried into RouterOS comments
    enabled  - false keeps the definition in git without provisioning it
    dhcp     - null disables DHCP for the VLAN, otherwise a pool/lease config
  EOT
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
  description = "Static routes: key is a stable label, value is destination + gateway."
  type = map(object({
    dst_address = string
    gateway     = string
    comment     = optional(string)
    distance    = optional(number)
  }))
  default = {}
}

variable "address_lists" {
  description = <<-EOT
    Port of VyOS `firewall group address-group`. Keyed by group name; each entry
    is a map of member label => address so adding/removing a host does not
    reshuffle Terraform addresses.
  EOT
  type        = map(map(string))
  default     = {}
}

variable "port_lists" {
  description = <<-EOT
    Port of VyOS `firewall group port-group`. RouterOS has no port-group object,
    so these are rendered into `dst_port` strings at plan time.
  EOT
  type        = map(list(string))
  default     = {}
}

variable "zone_policies" {
  description = <<-EOT
    Port of the VyOS zone-based firewall. Each entry is one directed zone pair
    (from -> to) and the accept rules that punch through its default drop.

    Ordering inside a pair is the list order. Global ordering across pairs is
    enforced by routeros_move_items using the sorted key of this map, so keep
    keys in `NNN-from-to` form.
  EOT
  type = map(object({
    from    = string
    to      = string
    comment = optional(string)
    # VyOS per-ruleset default-action. Most pairs are drop; the internet-egress
    # and VPN pairs were accept, and rendering those as drop would cut off the
    # internet for every VLAN once the terminal forward drop is in place.
    default_action = optional(string, "drop")
    log_default    = optional(bool, true)
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

  validation {
    condition     = alltrue([for p in values(var.zone_policies) : contains(["accept", "drop"], p.default_action)])
    error_message = "zone_policies[*].default_action must be accept or drop."
  }
}

variable "input_rules" {
  description = <<-EOT
    Explicit RouterOS `input` chain accepts, evaluated before the terminal input
    drop.

    This chain has no VyOS counterpart: VyOS had no local zone, so traffic TO
    the router was unfiltered, and RouterOS defconf is accept-all. Writing it
    explicitly is an intentional hardening improvement over the source config.

    It is also where the ported DNS rules land. VyOS sent client DNS to the
    `containers` zone (dnsdist at 10.10.53.4); native RouterOS DNS answers on
    the router itself, so those become router-destined input accepts.
  EOT
  type = map(object({
    comment = string
    # accept, or drop to carve an exception out of a later, broader accept
    # (e.g. no management from the camera VLAN ahead of "management from LAN").
    action            = optional(string, "accept")
    protocol          = optional(string)
    dst_port          = optional(string)
    dst_port_list     = optional(string)
    in_interface_list = optional(string)
    in_interface      = optional(string)
    src_address_list  = optional(string)
    src_address       = optional(string)
    icmp_options      = optional(string)
  }))
  default = {}

  validation {
    condition     = alltrue([for r in values(var.input_rules) : contains(["accept", "drop"], r.action)])
    error_message = "input_rules[*].action must be accept or drop."
  }
}

variable "input_drop_log" {
  description = "Log packets hitting the terminal input drop."
  type        = bool
  default     = true
}

variable "forward_drop_log" {
  description = "Log packets hitting the terminal forward drop (traffic between zones with no VyOS ruleset)."
  type        = bool
  default     = true
}

variable "dstnat_rules" {
  description = <<-EOT
    Port of VyOS `nat destination` (forced DNS / forced NTP interception).

    action is dst-nat (to_address required) or redirect (to the router itself,
    used once the resolver moved from the dnsdist container onto the router).
  EOT
  type = map(object({
    comment      = string
    in_interface = string
    protocol     = string
    dst_port     = string
    action       = optional(string, "dst-nat")
    dst_address  = optional(string)
    src_address  = optional(string)
    to_address   = optional(string)
    to_port      = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for r in values(var.dstnat_rules) :
      r.action == "redirect" || (r.action == "dst-nat" && r.to_address != null)
    ])
    error_message = "dstnat_rules: action must be redirect, or dst-nat with to_address set."
  }
}

variable "masquerade_out_interface" {
  description = <<-EOT
    Interface to srcnat/masquerade behind. VyOS had this commented out because
    the upstream EdgeRouter did the NAT; leave null to preserve that behaviour.
  EOT
  type        = string
  default     = null
}

variable "wireguard_interfaces" {
  description = <<-EOT
    WireGuard listeners. private_key comes from SOPS-decrypted tfvars, never
    from a literal in git.

    Deliberately NOT marked sensitive: Terraform forbids sensitive values as
    for_each arguments, and the map keys here are interface names. The private
    key is sensitive at the provider schema level, which is where it matters.
  EOT
  type = map(object({
    address     = string
    listen_port = number
    private_key = string
    # VyOS zone this listener belongs to (wg01 -> vpn, wg02 -> vpn-mobile).
    # Zone-pair and input rules name the zone, not the interface.
    zone    = optional(string)
    comment = optional(string)
    mtu     = optional(string)
    peers = map(object({
      public_key           = string
      allowed_address      = list(string)
      persistent_keepalive = optional(string, "15s")
      comment              = optional(string)
    }))
  }))
  default = {}
}

variable "dns" {
  description = <<-EOT
    RouterOS native resolver settings.

    upstream_servers  - forwarders used when no static record matches
    allow_remote      - serve LAN clients (required for the forced-DNS dstnat)
    static_records    - split-horizon overrides ported from blocky customDNS
    adlists           - blocklist URLs ported from blocky blackLists
  EOT
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
  description = "NTP client upstreams plus the server-mode toggle VyOS had enabled."
  type = object({
    servers     = list(string)
    server_mode = optional(bool, true)
    client_mode = optional(string, "unicast")
  })
}

variable "syslog" {
  description = "Remote syslog target ported from VyOS `system syslog host`."
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
  description = "SSH service port."
  type        = number
  default     = 22
}

variable "wan_add_default_route" {
  description = <<-EOT
    Whether the WAN DHCP lease installs a default route. RouterOS models this as
    a string ("yes" / "no" / "special-classless"), not a bool.

    "yes" because this router replaces the EdgeRouter Lite, which previously held
    the default route and handed it to VyOS over the transit VLAN. There is no
    upstream router left to learn it from.
  EOT
  type        = string
  default     = "yes"

  validation {
    condition     = contains(["yes", "no", "special-classless"], var.wan_add_default_route)
    error_message = "Must be yes, no, or special-classless."
  }
}

variable "lan_interface_lists" {
  description = <<-EOT
    Zone names whose interfaces make up the aggregate `zone-lan` interface list.

    Input-chain rules for router-provided services (DNS, NTP, DHCP) match on this
    list so they can never be satisfied from the WAN. The EdgeRouter Lite bound
    DNS and NTP to 0.0.0.0, which made it a public open resolver and an open NTP
    reflector. Scoping these to the LAN list is what closes that.
  EOT
  type        = list(string)
  default     = []
}

variable "connection_tracking" {
  description = <<-EOT
    Conntrack tuning carried over from the EdgeRouter Lite, which sized its table
    for the full internet-edge load.
  EOT
  type = object({
    # RouterOS models both of these as strings ("yes" / "no" / "auto"), not
    # booleans. Passing a bool fails at apply, not at validate.
    enabled                 = optional(string, "auto")
    loose_tcp_tracking      = optional(string)
    tcp_established_timeout = optional(string)
    tcp_close_wait_timeout  = optional(string)
    tcp_syn_sent_timeout    = optional(string)
    udp_timeout             = optional(string)
  })
  default = null
}

variable "ddns" {
  description = <<-EOT
    Dynamic DNS via a RouterOS script plus scheduler. Null disables it entirely.

    RouterOS has no Namecheap or Cloudflare DDNS client; the only native option is
    /ip cloud, which gives you <serial>.sn.mynetname.net instead of your own
    hostname. So a real hostname means a scripted provider.

    This is load-bearing, not cosmetic: WireGuard client configs use the DDNS
    hostname as their endpoint, so a stale record breaks remote VPN access at the
    next WAN address change.

    namecheap needs ddns_host + ddns_domain (sensitive, from SOPS). Uses the
    update endpoint at dynamicdns.park-your-domain.com, so no record lookup is
    required.

    cloudflare needs zone_id + record_id + record_name, and the A record must
    already exist. Create it in environments/prod/cloudflare, not here.
  EOT
  type = object({
    provider = string
    interval = optional(string, "5m")

    # cloudflare
    zone_id     = optional(string)
    record_id   = optional(string)
    record_name = optional(string)
    ttl         = optional(number, 120)
  })
  default = null

  validation {
    condition     = var.ddns == null ? true : contains(["namecheap", "cloudflare"], var.ddns.provider)
    error_message = "ddns.provider must be namecheap or cloudflare."
  }

  validation {
    condition = var.ddns == null ? true : (
      var.ddns.provider != "cloudflare" ||
      (var.ddns.zone_id != null && var.ddns.record_id != null && var.ddns.record_name != null)
    )
    error_message = "ddns.provider = cloudflare requires zone_id, record_id and record_name."
  }
}

variable "ddns_host" {
  description = "Namecheap DDNS host label (e.g. `ddns`). From SOPS."
  type        = string
  default     = null
  sensitive   = true
}

variable "ddns_domain" {
  description = "Namecheap DDNS domain. From SOPS: it ties the repo to an identity."
  type        = string
  default     = null
  sensitive   = true
}

variable "ddns_credential" {
  description = <<-EOT
    The DDNS secret: a Namecheap dynamic-DNS password, or a Cloudflare API token
    scoped to Zone:DNS:Edit. Comes from SOPS, never a literal.

    NOTE: this ends up stored in the RouterOS script body on the router, where any
    account with read+policy rights can display it. That is inherent to scripted
    DDNS on RouterOS, not a flaw in this module. Use a token scoped to exactly the
    one zone it needs, and rotate it if the router is ever RMA'd or resold.
  EOT
  type        = string
  default     = null
  sensitive   = true
}

variable "disabled_ip_services" {
  description = <<-EOT
    RouterOS services to disable, mapped to their default port. VyOS exposed
    only SSH, so everything MikroTik turns on by default is shut off here.

    A map rather than a list because the provider requires `port` on every
    ip_service entry, even when the only intent is to disable it.
  EOT
  type        = map(number)
  default = {
    telnet  = 23
    ftp     = 21
    www     = 80
    api     = 8728
    api-ssl = 8729
  }
}

# The admin account is three variables rather than a map keyed by username:
# the username comes from SOPS, sops_file marks everything it returns as
# sensitive, and Terraform refuses sensitive values as for_each keys.
variable "admin_username" {
  description = "Interactive admin account name (from SOPS)."
  type        = string
  default     = null
  sensitive   = true
}

variable "admin_password" {
  description = <<-EOT
    Password for the admin account (from SOPS). Required: a RouterOS user created
    without one has an EMPTY password, which Winbox/WebFig will accept. SSH still
    uses keys only (routeros_ip_ssh_server below).
  EOT
  type        = string
  default     = null
  sensitive   = true
}

variable "admin_ssh_keys" {
  description = "SSH public keys for the admin account."
  type        = list(string)
  default     = []
}

# --- Monitoring (mktxp) -------------------------------------------------------
# mktxp scrapes RouterOS over its native API, not REST. It replaces the VyOS
# node-exporter, and its DHCP lease metric replaces VyOS's dhcp_lease_info
# (flow-collector client names).
variable "monitoring_username" {
  description = "Read-only API account for mktxp. null disables it and keeps api-ssl off."
  type        = string
  default     = null
}

variable "monitoring_source_list" {
  description = "Address list allowed to reach the read-only API (api-ssl 8729)."
  type        = string
  default     = null
}

variable "monitoring_password" {
  description = "Password for the mktxp account (from SOPS)."
  type        = string
  default     = null
  sensitive   = true
}

variable "api_certificate" {
  description = "Certificate for the api-ssl service; the bootstrap script creates `rest-api`."
  type        = string
  default     = "rest-api"
}

variable "fasttrack" {
  description = <<-EOT
    Add the FastTrack (hw_offload) rule for established/related forward traffic.
    FastTracked packets bypass ip traffic-flow, so turning this on blinds the
    flow export (var.flow_export) to bulk transfers. Keep it off unless the
    RB5009 can't route the WAN rate without it; check CPU under a speed test.
  EOT
  type        = bool
  default     = false
}

variable "flow_export" {
  description = <<-EOT
    NetFlow/IPFIX export of WAN-bound flows to tnwks-ops monitoring/flow-collector
    (WAN top talkers in War Room alerts). Exported on the WAN interface, so
    only internet traffic is seen; forward-chain records carry the LAN client
    address (srcnat happens after traffic-flow). null disables it.
  EOT
  type = object({
    collector_address = string # Windows host that relays udp into WSL
    collector_port    = optional(number, 2055)
    src_address       = optional(string) # e.g. the 910 gateway address
    version           = optional(string, "IPFIX")
    interfaces        = optional(string) # defaults to var.wan_interface
    # Export long flows every minute so per-client rates stay near real time.
    active_flow_timeout   = optional(string, "1m")
    inactive_flow_timeout = optional(string, "15s")
  })
  default = null
}
