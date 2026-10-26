# Zone-based firewall, ported from VyOS `firewall zone` + `firewall ipv4 name`.
#
# ============================================================================
# HOW THE PORT WORKS (read this before editing rules)
# ============================================================================
#
# VyOS model:
#   * `firewall zone <z>` groups interfaces and has default-action drop.
#   * `firewall zone <to> from <from> firewall name <ruleset>` attaches a named
#     ruleset to a directed zone pair.
#   * Each ruleset has its own default-action, so traffic that matches no
#     accept rule in that pair is dropped without affecting other pairs.
#   * `firewall global-options state-policy` handled established/related/invalid
#     once, globally.
#
# RouterOS model:
#   * One flat, ordered `forward` chain. No zones, no per-pair defaults.
#
# The port therefore does three things:
#   1. Global state policy becomes the first three forward-chain rules
#      (fasttrack + accept established/related, drop invalid).
#   2. Each VyOS zone becomes a RouterOS interface-list (see interfaces.tf), so
#      a rule can say in_interface_list=zone-x, out_interface_list=zone-y and
#      mean exactly what the VyOS pair meant.
#   3. Each zone pair contributes its accept rules followed by its own
#      terminating default (drop, or accept where VyOS defaulted the pair
#      open). That per-pair default is what reproduces VyOS's per-pair
#      default-action. Without it, an unmatched packet would fall through to
#      later pairs' accepts and the policy would be wrong.
#   4. A terminal forward drop after every pair. VyOS dropped traffic between
#      zones with no `from` binding at all (110 of its 140 rulesets are empty
#      default-drop pairs, and most zone pairs have no ruleset). RouterOS
#      forward defaults to ACCEPT, so without this rule every one of those
#      pairs, and WAN -> LAN, would be wide open.
#
# ORDER IS SEMANTIC. Terraform does not guarantee resource creation order, so
# routeros_move_items reorders the chain to the sorted key sequence after
# apply. Zone-pair keys are `NNN-from-to`; keep them numbered.

# --- Address lists (VyOS firewall group address-group) -----------------------

resource "routeros_ip_firewall_addr_list" "this" {
  for_each = local.address_list_members

  list    = each.value.list
  address = each.value.address
  comment = each.value.comment
}

# --- Global state policy -----------------------------------------------------
# VyOS: state-policy established accept / related accept / invalid drop.

# FastTrack (and its hw_offload) bypasses ip traffic-flow, so with it on the
# flow export only sees the first packets of each connection and the WAN
# top-talker attribution (tnwks-ops monitoring/flow-collector) goes blind.
# Off by default; see var.fasttrack.
resource "routeros_ip_firewall_filter" "fasttrack_established" {
  count            = var.fasttrack ? 1 : 0
  chain            = "forward"
  action           = "fasttrack-connection"
  connection_state = "established,related"
  hw_offload       = true
  comment          = "000 state: fasttrack established,related (terraform)"
}

resource "routeros_ip_firewall_filter" "accept_established" {
  chain            = "forward"
  action           = "accept"
  connection_state = "established,related"
  comment          = "001 state: accept established,related (terraform)"
}

resource "routeros_ip_firewall_filter" "drop_invalid" {
  chain            = "forward"
  action           = "drop"
  connection_state = "invalid"
  comment          = "002 state: drop invalid (terraform)"
}

# --- Per-pair accept rules ---------------------------------------------------

resource "routeros_ip_firewall_filter" "zone_accept" {
  for_each = local.zone_rules

  chain              = "forward"
  action             = "accept"
  in_interface_list  = routeros_interface_list.zone[each.value.from].name
  out_interface_list = routeros_interface_list.zone[each.value.to].name

  protocol         = each.value.protocol
  src_address      = each.value.src_address
  dst_address      = each.value.dst_address
  src_address_list = each.value.src_address_list
  dst_address_list = each.value.dst_address_list
  dst_port         = each.value.dst_port

  comment = "${each.key} ${each.value.comment} (terraform)"

  depends_on = [routeros_ip_firewall_addr_list.this]
}

# --- Per-pair terminating drop ----------------------------------------------
# This is the VyOS per-ruleset default-action. Do not remove it: the accepts
# above are only correct because the pair cannot fall through.

resource "routeros_ip_firewall_filter" "zone_default" {
  for_each = local.zone_defaults

  chain              = "forward"
  action             = each.value.action
  in_interface_list  = routeros_interface_list.zone[each.value.from].name
  out_interface_list = routeros_interface_list.zone[each.value.to].name
  log                = each.value.log
  log_prefix         = "${each.value.from}-${each.value.to}-default"
  comment            = "${each.key}/zzz ${each.value.comment} (terraform)"

  # Appended after the pair's accepts on first apply, so the chain is in a
  # sane order even before routeros_move_items runs.
  depends_on = [routeros_ip_firewall_filter.zone_accept]
}

# --- Terminal forward drop ---------------------------------------------------
# Everything no zone pair claimed. See point 4 at the top of this file.

resource "routeros_ip_firewall_filter" "forward_drop" {
  chain      = "forward"
  action     = "drop"
  log        = var.forward_drop_log
  log_prefix = "forward-default"
  comment    = "zzz forward: default drop (terraform)"

  depends_on = [
    routeros_ip_firewall_filter.accept_established,
    routeros_ip_firewall_filter.zone_accept,
    routeros_ip_firewall_filter.zone_default,
  ]
}

# --- Input chain -------------------------------------------------------------
# No VyOS counterpart. VyOS had no local zone so input was unfiltered, and
# RouterOS defconf accepts everything to the router. This chain is an
# intentional hardening delta, and it is where the ported client-DNS rules land
# now that the resolver is the router instead of the dnsdist container.

resource "routeros_ip_firewall_filter" "input_established" {
  chain            = "input"
  action           = "accept"
  connection_state = "established,related"
  comment          = "000 input: accept established,related (terraform)"
}

resource "routeros_ip_firewall_filter" "input_drop_invalid" {
  chain            = "input"
  action           = "drop"
  connection_state = "invalid"
  comment          = "001 input: drop invalid (terraform)"
}

# The out-of-band port always gets SSH, REST and Winbox. This is the rule that
# keeps Terraform's own session alive through the first apply, and the cable to
# plug into if a later change to the input chain goes wrong.
resource "routeros_ip_firewall_filter" "input_mgmt" {
  count = var.mgmt == null ? 0 : 1

  chain        = "input"
  action       = "accept"
  protocol     = "tcp"
  dst_port     = "22,443,8291"
  in_interface = var.mgmt.interface
  comment      = "002 input: SSH/REST/Winbox on the out-of-band mgmt port (terraform)"
}

# WireGuard on the out-of-band port as well, so the admin VPN can be tested end
# to end on the bench (and used over a direct cable) before the router is ever
# on the network.
resource "routeros_ip_firewall_filter" "input_mgmt_wireguard" {
  count = var.mgmt == null || length(var.wireguard_interfaces) == 0 ? 0 : 1

  chain        = "input"
  action       = "accept"
  protocol     = "udp"
  dst_port     = join(",", sort([for w in values(var.wireguard_interfaces) : tostring(w.listen_port)]))
  in_interface = var.mgmt.interface
  comment      = "003 input: WireGuard on the out-of-band mgmt port (terraform)"
}

# --- Aggregate LAN interface list --------------------------------------------
# Router services (DNS, NTP, DHCP) must be reachable from internal VLANs and
# never from the WAN, where an answering resolver or NTP server is a reflection
# source. Matching the input chain against this list enforces that.

resource "routeros_interface_list" "lan" {
  name    = "zone-lan"
  comment = "All internal VLANs, for input-chain service scoping (terraform)"
}

resource "routeros_interface_list_member" "lan" {
  # Keyed off local.active_vlans rather than var.vlans so this can never name an
  # SVI that was not created (disabled VLANs have no interface to reference).
  for_each = toset([
    for zone in var.lan_interface_lists : zone
    if contains(keys(local.active_vlans), zone)
  ])

  list      = routeros_interface_list.lan.name
  interface = routeros_interface_vlan.svi[each.key].name

  depends_on = [routeros_interface_vlan.svi]
}

# The native network is internal too (the switch uses router DNS/NTP).
resource "routeros_interface_list_member" "lan_native" {
  count = local.native_enabled ? 1 : 0

  list      = routeros_interface_list.lan.name
  interface = routeros_interface_bridge.lan.name
}

resource "routeros_ip_firewall_filter" "input_accept" {
  for_each = var.input_rules

  chain             = "input"
  action            = each.value.action
  protocol          = each.value.protocol
  dst_port          = each.value.dst_port_list != null ? local.port_list_strings[each.value.dst_port_list] : each.value.dst_port
  in_interface_list = each.value.in_interface_list != null ? "zone-${each.value.in_interface_list}" : null
  # WireGuard is the one service that must answer on the WAN, so it matches a
  # physical interface instead of a zone list.
  in_interface     = each.value.in_interface
  src_address_list = each.value.src_address_list
  src_address      = each.value.src_address
  icmp_options     = each.value.icmp_options
  comment          = "${each.key} ${each.value.comment} (terraform)"

  depends_on = [
    routeros_interface_list.zone,
    routeros_interface_list_member.lan,
    routeros_ip_firewall_addr_list.this,
  ]
}

# mktxp's API-SSL port, from the monitoring source only. Exists only while the
# monitoring account does.
resource "routeros_ip_firewall_filter" "input_monitoring" {
  count = local.monitoring_enabled ? 1 : 0

  chain             = "input"
  action            = "accept"
  protocol          = "tcp"
  dst_port          = "8729"
  in_interface_list = routeros_interface_list.lan.name
  src_address_list  = var.monitoring_source_list
  comment           = "600 input: RouterOS API-SSL for mktxp (terraform)"
}

resource "routeros_ip_firewall_filter" "input_drop" {
  chain      = "input"
  action     = "drop"
  log        = var.input_drop_log
  log_prefix = "input-default"
  comment    = "zzz input: default drop (terraform)"

  # New filter rules append to the end of the chain. If this drop were created
  # before the established/mgmt accepts, Terraform's next REST call would be
  # dropped mid-apply, before routeros_move_items ever got to reorder anything.
  depends_on = [
    routeros_ip_firewall_filter.input_established,
    routeros_ip_firewall_filter.input_drop_invalid,
    routeros_ip_firewall_filter.input_mgmt,
    routeros_ip_firewall_filter.input_mgmt_wireguard,
    routeros_ip_firewall_filter.input_accept,
    routeros_ip_firewall_filter.input_monitoring,
  ]
}

# --- Ordering ---------------------------------------------------------------
# Sequence: input chain (state, mgmt, accepts, drop), then forward chain (state,
# then for each zone pair its accepts followed by its own default, then the
# terminal drop).

locals {
  firewall_sequence = concat(
    [
      routeros_ip_firewall_filter.input_established.id,
      routeros_ip_firewall_filter.input_drop_invalid.id,
    ],
    [for r in routeros_ip_firewall_filter.input_mgmt : r.id],
    [for r in routeros_ip_firewall_filter.input_mgmt_wireguard : r.id],
    [
      for k in sort(keys(var.input_rules)) :
      routeros_ip_firewall_filter.input_accept[k].id
    ],
    [for r in routeros_ip_firewall_filter.input_monitoring : r.id],
    [
      routeros_ip_firewall_filter.input_drop.id,
    ],
    [for r in routeros_ip_firewall_filter.fasttrack_established : r.id],
    [
      routeros_ip_firewall_filter.accept_established.id,
      routeros_ip_firewall_filter.drop_invalid.id,
    ],
    flatten([
      for pair_key in sort(keys(local.active_zone_policies)) : concat(
        [
          for rule_key in sort([for k, v in local.zone_rules : k if v.pair_key == pair_key]) :
          routeros_ip_firewall_filter.zone_accept[rule_key].id
        ],
        [routeros_ip_firewall_filter.zone_default[pair_key].id],
      )
    ]),
    [routeros_ip_firewall_filter.forward_drop.id],
  )
}

resource "routeros_move_items" "firewall_filter" {
  resource_path = "/ip/firewall/filter"
  sequence      = local.firewall_sequence

  depends_on = [
    routeros_ip_firewall_filter.input_established,
    routeros_ip_firewall_filter.input_drop_invalid,
    routeros_ip_firewall_filter.input_mgmt,
    routeros_ip_firewall_filter.input_mgmt_wireguard,
    routeros_ip_firewall_filter.input_accept,
    routeros_ip_firewall_filter.input_monitoring,
    routeros_ip_firewall_filter.input_drop,
    routeros_ip_firewall_filter.fasttrack_established,
    routeros_ip_firewall_filter.accept_established,
    routeros_ip_firewall_filter.drop_invalid,
    routeros_ip_firewall_filter.zone_accept,
    routeros_ip_firewall_filter.zone_default,
    routeros_ip_firewall_filter.forward_drop,
  ]
}

# --- Connection tracking -----------------------------------------------------
# The EdgeRouter Lite tuned conntrack for the full internet-edge load
# (table-size 32768, hash-size 4096, tcp loose enable). RouterOS sizes its table
# automatically from available RAM, so only the behavioural knobs carry over.

resource "routeros_ip_firewall_connection_tracking" "this" {
  count = var.connection_tracking != null ? 1 : 0

  enabled                 = var.connection_tracking.enabled
  loose_tcp_tracking      = var.connection_tracking.loose_tcp_tracking
  tcp_established_timeout = var.connection_tracking.tcp_established_timeout
  tcp_close_wait_timeout  = var.connection_tracking.tcp_close_wait_timeout
  tcp_syn_sent_timeout    = var.connection_tracking.tcp_syn_sent_timeout
  udp_timeout             = var.connection_tracking.udp_timeout
}
