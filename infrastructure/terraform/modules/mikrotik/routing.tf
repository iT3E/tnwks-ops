# Static routing, ported from VyOS `protocols static route`.
#
# VyOS:
#   0.0.0.0/0        -> 172.16.1.1      (EdgeRouter Lite, the actual edge)
#   10.10.93.0/24    -> 172.16.1.254
#   10.60.10.0/24    -> 10.10.140.140   (EP-R6 wireless bridge to in-laws)
#   10.98.0.0/24     -> 172.16.1.254  (dead; the subnet is connected on bare eth1)

resource "routeros_ip_route" "static" {
  for_each = var.static_routes

  dst_address = each.value.dst_address
  gateway     = each.value.gateway
  distance    = each.value.distance
  comment     = join(" ", compact([each.key, each.value.comment, "(terraform)"]))

  depends_on = [routeros_ip_address.svi]
}

# --- Anti-spoofing -----------------------------------------------------------
# Strict reverse-path filtering drops a packet whose source would not be routed
# back out the interface it arrived on, e.g. something on the WAN claiming an
# internal address. Single router, no asymmetric paths, so strict is safe. ICMP
# redirects have no place on a border router.
resource "routeros_ip_settings" "this" {
  rp_filter        = "strict"
  send_redirects   = false
  accept_redirects = false
}
