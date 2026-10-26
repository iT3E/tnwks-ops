# WireGuard, ported from VyOS `interfaces wireguard wg01/wg02`.
#
# VyOS carried wg01 (WIREGUARD: it-pc01, macbook-it, xps-it) and wg02
# (WIREGUARD-AO: it-mobile, mh-mobile). Both zones existed in the zone firewall
# as `vpn` (wg01) and `vpn-mobile` (wg02), so the zone_policies keys use those
# names and the interface-list wiring in interfaces.tf picks them up.
#
# Private keys are never literals here. They arrive through SOPS-decrypted
# tfvars; see the environment README for the decrypt step.

resource "routeros_interface_wireguard" "this" {
  for_each = var.wireguard_interfaces

  name        = each.key
  listen_port = each.value.listen_port
  private_key = each.value.private_key
  mtu         = each.value.mtu
  comment     = coalesce(each.value.comment, each.key)
}

resource "routeros_ip_address" "wireguard" {
  for_each = var.wireguard_interfaces

  address   = each.value.address
  interface = routeros_interface_wireguard.this[each.key].name
  comment   = coalesce(each.value.comment, each.key)
}

resource "routeros_interface_wireguard_peer" "this" {
  for_each = merge([
    for iface_key, iface in var.wireguard_interfaces : {
      for peer_key, peer in iface.peers :
      "${iface_key}/${peer_key}" => {
        interface            = iface_key
        name                 = peer_key
        public_key           = peer.public_key
        allowed_address      = peer.allowed_address
        persistent_keepalive = peer.persistent_keepalive
        comment              = coalesce(peer.comment, peer_key)
      }
    }
  ]...)

  interface            = routeros_interface_wireguard.this[each.value.interface].name
  name                 = each.value.name
  public_key           = each.value.public_key
  allowed_address      = each.value.allowed_address
  persistent_keepalive = each.value.persistent_keepalive
  comment              = each.value.comment
}
