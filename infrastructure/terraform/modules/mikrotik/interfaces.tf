# L2/L3: bridge, VLAN sub-interfaces, SVI addresses, WAN DHCP client.
# Port of VyOS: interfaces ethernet eth0/eth1 + vif <id>.
#
# Structural difference worth understanding before reading further:
#
#   VyOS terminated VLANs directly on a physical port (eth1.910) and had no
#   L2 bridge at all. RouterOS on an RB5009 wants a VLAN-filtering bridge so\n#   the switch chip can hardware-offload the trunk. So each VyOS `eth1.<vif>`
#   becomes a routeros_interface_vlan on the bridge, and each VyOS
#   `address` becomes a separate routeros_ip_address.

resource "routeros_interface_bridge" "lan" {
  name           = var.bridge_name
  vlan_filtering = true
  # RouterOS drops untagged frames into pvid; nothing on this trunk is
  # untagged, so pin pvid to an unused tag rather than the default 1.
  pvid    = 4094
  comment = "LAN trunk to Aruba SW03 (terraform)"
}

resource "routeros_interface_bridge_port" "lan" {
  for_each = toset(var.bridge_ports)

  bridge    = routeros_interface_bridge.lan.name
  interface = each.value
  pvid      = 4094
  # hw=true keeps the port on the switch chip for offloaded forwarding.
  hw      = true
  comment = "trunk member (terraform)"
}

# Tagged VLAN membership on the bridge. Without this the bridge drops tagged
# frames once vlan_filtering is on, which is the classic "everything died after
# I enabled VLAN filtering" RouterOS footgun.
resource "routeros_interface_bridge_vlan" "tagged" {
  for_each = local.active_vlans

  bridge = routeros_interface_bridge.lan.name
  # Provider expects a set of strings, and accepts ranges like "100-103".
  vlan_ids = [tostring(each.value.vlan_id)]
  tagged   = concat([routeros_interface_bridge.lan.name], var.bridge_ports)
  comment  = coalesce(each.value.comment, each.key)

  depends_on = [routeros_interface_bridge_port.lan]
}

resource "routeros_interface_vlan" "svi" {
  for_each = local.active_vlans

  name      = local.zone_interfaces[each.key]
  interface = routeros_interface_bridge.lan.name
  vlan_id   = each.value.vlan_id
  comment   = coalesce(each.value.comment, each.key)
}

resource "routeros_ip_address" "svi" {
  for_each = local.active_vlans

  address   = each.value.address
  interface = routeros_interface_vlan.svi[each.key].name
  comment   = coalesce(each.value.comment, each.key)
}

# --- WAN ---------------------------------------------------------------------
# VyOS had eth0 as `address dhcp` facing the cable modem. The transit path to
# the EdgeRouter is a VLAN SVI (transit-10), handled above.

resource "routeros_ip_dhcp_client" "wan" {
  interface = var.wan_interface
  comment   = "WAN - Cable (terraform)"

  # VyOS took a default route from the EdgeRouter via transit-10, not from the
  # cable modem. Keep the WAN lease address-only so it cannot install a
  # competing default route. RouterOS models this as a string, not a bool.
  add_default_route = "no"
  use_peer_dns      = false
  use_peer_ntp      = false
}

# --- Zone interface lists ----------------------------------------------------
# RouterOS has no zone object. An interface-list per VyOS zone gives the
# firewall rules the same from/to vocabulary.

resource "routeros_interface_list" "zone" {
  for_each = local.zone_interfaces

  name    = "zone-${each.key}"
  comment = "VyOS zone ${each.key} (terraform)"
}

resource "routeros_interface_list_member" "zone" {
  for_each = local.zone_interfaces

  list      = routeros_interface_list.zone[each.key].name
  interface = each.value

  depends_on = [
    routeros_interface_vlan.svi,
    routeros_interface_wireguard.this,
  ]
}
