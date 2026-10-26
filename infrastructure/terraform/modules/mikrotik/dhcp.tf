# DHCP server per routed VLAN.
# Port of VyOS: service dhcp-server shared-network-name <zone>.
#
# VyOS expressed one `shared-network-name` per VLAN with an inline range and
# static-mappings. RouterOS splits this into three objects:
#   ip_pool                  <- the range
#   ip_dhcp_server           <- the listener bound to the SVI
#   ip_dhcp_server_network   <- gateway / dns / domain options
#   ip_dhcp_server_lease     <- static mappings

resource "routeros_ip_pool" "dhcp" {
  for_each = local.dhcp_vlans

  name    = "pool-${each.key}"
  ranges  = ["${each.value.dhcp.pool_start}-${each.value.dhcp.pool_end}"]
  comment = coalesce(each.value.comment, each.key)
}

resource "routeros_ip_dhcp_server" "this" {
  for_each = local.dhcp_vlans

  name          = "dhcp-${each.key}"
  interface     = routeros_interface_vlan.svi[each.key].name
  address_pool  = routeros_ip_pool.dhcp[each.key].name
  lease_time    = each.value.dhcp.lease_time
  authoritative = each.value.dhcp.authoritative
  # VyOS had ping-check enabled on every shared-network.
  conflict_detection = true
  comment            = coalesce(each.value.comment, each.key)
}

resource "routeros_ip_dhcp_server_network" "this" {
  for_each = local.dhcp_vlans

  # RouterOS keys the network option-set by subnet, not by server name.
  address = cidrsubnet(each.value.address, 0, 0)
  gateway = split("/", each.value.address)[0]
  # VyOS handed out 10.10.53.4 (dnsdist). Post-migration that is the router
  # itself unless the tfvars override it, which is what makes the forced-DNS
  # dstnat rules coherent.
  dns_server = coalesce(
    each.value.dhcp.dns_servers,
    [split("/", each.value.address)[0]],
  )
  ntp_server = each.value.dhcp.ntp_servers
  domain     = var.domain
  comment    = coalesce(each.value.comment, each.key)
}

resource "routeros_ip_dhcp_server_lease" "static" {
  for_each = local.dhcp_leases

  server      = routeros_ip_dhcp_server.this[each.value.vlan_key].name
  address     = each.value.address
  mac_address = upper(each.value.mac_address)
  comment     = each.value.comment
}
