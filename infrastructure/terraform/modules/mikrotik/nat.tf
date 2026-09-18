# NAT, ported from VyOS `nat destination` (and the commented-out source rule).
#
# VyOS used destination NAT for two captive-service patterns:
#   * Force DNS  - redirect any :53 that is not already headed to the resolver
#   * Force NTP  - redirect any :123 that is not already headed to the SVI
#
# The `destination address '!x.x.x.x'` negation becomes RouterOS
# `dst_address = "!x.x.x.x"`, which the provider passes through verbatim.

resource "routeros_ip_firewall_nat" "dstnat" {
  for_each = var.dstnat_rules

  chain        = "dstnat"
  action       = "dst-nat"
  in_interface = each.value.in_interface
  protocol     = each.value.protocol
  dst_port     = each.value.dst_port
  dst_address  = each.value.dst_address
  src_address  = each.value.src_address
  to_addresses = each.value.to_address
  to_ports     = each.value.to_port
  comment      = "${each.key} ${each.value.comment} (terraform)"
}

resource "routeros_move_items" "nat" {
  resource_path = "/ip/firewall/nat"
  sequence      = [for k in sort(keys(var.dstnat_rules)) : routeros_ip_firewall_nat.dstnat[k].id]

  depends_on = [routeros_ip_firewall_nat.dstnat]
}

# --- Source NAT --------------------------------------------------------------
# VyOS had `nat source rule 100` commented out because the upstream EdgeRouter
# performs the outbound NAT. Leaving masquerade_out_interface null preserves
# that. Set it only if the MikroTik becomes the edge device.

resource "routeros_ip_firewall_nat" "masquerade" {
  count = var.masquerade_out_interface == null ? 0 : 1

  chain         = "srcnat"
  action        = "masquerade"
  out_interface = var.masquerade_out_interface
  comment       = "900 LAN -> WAN masquerade (terraform)"
}
