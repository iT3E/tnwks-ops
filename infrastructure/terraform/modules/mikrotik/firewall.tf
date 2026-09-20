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
#      terminating drop. That per-pair drop is what reproduces VyOS's per-pair
#      default-action. Without it, an unmatched packet would fall through to
#      later pairs' accepts and the policy would be wrong.
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

resource "routeros_ip_firewall_filter" "fasttrack_established" {
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

resource "routeros_ip_firewall_filter" "zone_drop" {
  for_each = local.zone_drops

  chain              = "forward"
  action             = "drop"
  in_interface_list  = routeros_interface_list.zone[each.value.from].name
  out_interface_list = routeros_interface_list.zone[each.value.to].name
  log                = each.value.log
  log_prefix         = "${each.value.from}-${each.value.to}-default"
  comment            = "${each.key}/zzz ${each.value.comment} (terraform)"
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

# --- Aggregate LAN interface list --------------------------------------------
# Router services (DNS, NTP, DHCP) must be reachable from every internal VLAN and
# from none of the WAN. The EdgeRouter Lite bound DNS and NTP to 0.0.0.0, which
# made it a public open resolver and an open NTP reflector. Matching the input
# chain against this list is what stops that from being recreated here.

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

resource "routeros_ip_firewall_filter" "input_accept" {
  for_each = var.input_rules

  chain             = "input"
  action            = "accept"
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

resource "routeros_ip_firewall_filter" "input_drop" {
  chain      = "input"
  action     = "drop"
  log        = var.input_drop_log
  log_prefix = "input-default"
  comment    = "zzz input: default drop (terraform)"
}

# --- Ordering ---------------------------------------------------------------
# Sequence: input chain (state, accepts, drop), then forward chain (state, then
# for each zone pair its accepts followed by its own drop).

locals {
  firewall_sequence = concat(
    [
      routeros_ip_firewall_filter.input_established.id,
      routeros_ip_firewall_filter.input_drop_invalid.id,
    ],
    [
      for k in sort(keys(var.input_rules)) :
      routeros_ip_firewall_filter.input_accept[k].id
    ],
    [
      routeros_ip_firewall_filter.input_drop.id,
      routeros_ip_firewall_filter.fasttrack_established.id,
      routeros_ip_firewall_filter.accept_established.id,
      routeros_ip_firewall_filter.drop_invalid.id,
    ],
    flatten([
      for pair_key in sort(keys(local.active_zone_policies)) : concat(
        [
          for rule_key in sort([for k, v in local.zone_rules : k if v.pair_key == pair_key]) :
          routeros_ip_firewall_filter.zone_accept[rule_key].id
        ],
        [routeros_ip_firewall_filter.zone_drop[pair_key].id],
      )
    ]),
  )
}

resource "routeros_move_items" "firewall_filter" {
  resource_path = "/ip/firewall/filter"
  sequence      = local.firewall_sequence

  depends_on = [
    routeros_ip_firewall_filter.input_established,
    routeros_ip_firewall_filter.input_drop_invalid,
    routeros_ip_firewall_filter.input_accept,
    routeros_ip_firewall_filter.input_drop,
    routeros_ip_firewall_filter.fasttrack_established,
    routeros_ip_firewall_filter.accept_established,
    routeros_ip_firewall_filter.drop_invalid,
    routeros_ip_firewall_filter.zone_accept,
    routeros_ip_firewall_filter.zone_drop,
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
