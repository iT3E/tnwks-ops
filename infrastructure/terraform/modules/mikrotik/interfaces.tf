# L2/L3: bridge, VLAN sub-interfaces, SVI addresses, WAN DHCP client.
# Port of VyOS: interfaces ethernet eth0/eth1 + vif <id>.
#
# Structural difference worth understanding before reading further:
#
#   VyOS terminated VLANs directly on a physical port (eth1.910) and had no
#   L2 bridge at all. RouterOS on an RB5009 wants a VLAN-filtering bridge so
#   the switch chip can hardware-offload the trunk. So each VyOS `eth1.<vif>`
#   becomes a routeros_interface_vlan on the bridge, and each VyOS
#   `address` becomes a separate routeros_ip_address.

resource "routeros_interface_bridge" "lan" {
  name           = var.bridge_name
  vlan_filtering = true
  # The bridge's own L3 interface sits in its pvid. With a native network that
  # is VLAN 1 (routed here, see var.native_vlan); without one, pin it to an
  # unused tag so nothing lands in VLAN 1 by accident.
  pvid    = local.native_enabled ? 1 : 4094
  comment = "LAN trunk to Aruba SW03 (terraform)"
}

locals {
  native_enabled = var.native_vlan != null
  native_ports   = local.native_enabled ? toset(var.native_vlan.ports) : toset([])
}

resource "routeros_interface_bridge_port" "lan" {
  for_each = toset(var.bridge_ports)

  lifecycle {
    precondition {
      condition     = var.mgmt == null || !contains(var.bridge_ports, try(var.mgmt.interface, ""))
      error_message = "The mgmt port must not be a bridge member: its address goes invalid when bridged, locking Terraform out."
    }
  }

  bridge    = routeros_interface_bridge.lan.name
  interface = each.value
  # Untagged frames: native network on the uplink, black-holed everywhere else.
  pvid = contains(local.native_ports, each.value) ? 1 : 4094
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

# Native network: VLAN 1, untagged on the uplink and on the bridge's own
# interface, which carries the address.
resource "routeros_interface_bridge_vlan" "native" {
  count = local.native_enabled ? 1 : 0

  bridge   = routeros_interface_bridge.lan.name
  vlan_ids = ["1"]
  untagged = concat([routeros_interface_bridge.lan.name], var.native_vlan.ports)
  comment  = coalesce(var.native_vlan.comment, var.native_vlan.zone)

  lifecycle {
    precondition {
      condition     = alltrue([for p in var.native_vlan.ports : contains(var.bridge_ports, p)])
      error_message = "native_vlan.ports must all be bridge_ports."
    }
  }

  depends_on = [routeros_interface_bridge_port.lan]
}

resource "routeros_ip_address" "native" {
  count = local.native_enabled ? 1 : 0

  address   = var.native_vlan.address
  interface = routeros_interface_bridge.lan.name
  comment   = coalesce(var.native_vlan.comment, var.native_vlan.zone)
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

  # The EdgeRouter Lite used to own the default route and hand it to VyOS over
  # transit-10. The RB5009 replaces both boxes, so the default route now comes
  # from the WAN lease itself. RouterOS models this as a string, not a bool.
  add_default_route = var.wan_add_default_route
  use_peer_dns      = false
  use_peer_ntp      = false
}

# Fallback default via the EdgeRouter while it is still upstream. Distance 10
# loses to the DHCP lease's distance-1 route as soon as the modem is on ether1.
resource "routeros_ip_route" "upstream_default" {
  count = var.upstream_gateway == null ? 0 : 1

  dst_address = "0.0.0.0/0"
  gateway     = var.upstream_gateway
  distance    = 10
  comment     = "default via upstream router during staged cutover (terraform)"

  depends_on = [routeros_ip_address.svi]
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

# The WAN port joins the outside zone, so VyOS's `X -> transit-10` rules keep
# governing internet egress after the modem moves here (var.outside_zone).
resource "routeros_interface_list_member" "outside_wan" {
  count = var.outside_zone == null ? 0 : 1

  list      = routeros_interface_list.zone[var.outside_zone].name
  interface = var.wan_interface
}
