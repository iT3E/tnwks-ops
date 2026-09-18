# DNS and NTP.
#
# ============================================================================
# THE BIGGEST DESIGN CHANGE IN THIS PORT
# ============================================================================
#
# VyOS ran a three-container DNS stack in Podman on the router itself:
#
#   dnsdist  10.10.53.4  - the address every DHCP scope handed out. Split
#                          traffic by client subnet into pools.
#   blocky   10.10.53.7  - ad/tracker blocking + split-horizon customDNS
#                          (unms/uisp.tnwks.us -> 10.10.91.1,
#                           *.internal.tnwks.us -> 10.10.91.142)
#   bind     10.10.53.3  - authoritative for tnwks.local and the `unifi` zone
#
# That stack was a major contributor to the router being pinned at ~187% CPU.
# An RB5009 has 1GB RAM, no meaningful onboard storage for container layers,
# and RouterOS containers need an external disk to be sane. Rebuilding a
# three-container DNS chain on it would be strictly worse than what it replaced.
#
# So this port keeps DNS on the router but drops the container chain:
#
#   dnsdist's job  -> routeros_ip_dns.servers (DoH/DoT upstreams) and, where
#                     per-subnet answers are genuinely needed,
#                     routeros_ip_dns_forwarders.
#   blocky blocking-> routeros_ip_dns_adlist (native RouterOS 7.15+ adlist)
#   blocky customDNS / bind zones
#                  -> routeros_ip_dns_record static entries
#
# The router IP becomes the resolver each DHCP scope advertises, which is why
# dhcp.tf defaults dns_server to the SVI address and why the forced-DNS dstnat
# rules in nat.tf now redirect to the SVI instead of 10.10.53.4.
#
# What is deliberately NOT ported: the `zip` DropAction and the pool-per-subnet
# ControlD paths from dnsdist.conf. If those matter, they come back as
# ip_dns_forwarders entries plus a mangle/routing-mark, not as a container.

resource "routeros_ip_dns" "this" {
  servers               = var.dns.upstream_servers
  allow_remote_requests = var.dns.allow_remote
  cache_size            = var.dns.cache_size
  cache_max_ttl         = var.dns.cache_max_ttl
  use_doh_server        = var.dns.use_doh_server
  verify_doh_cert       = var.dns.verify_doh_cert
}

resource "routeros_ip_dns_record" "static" {
  for_each = var.dns.static_records

  name            = each.value.name
  type            = each.value.type
  address         = each.value.address
  cname           = each.value.cname
  match_subdomain = each.value.match_subdomain
  ttl             = each.value.ttl
  comment         = coalesce(each.value.comment, each.key)

  depends_on = [routeros_ip_dns.this]
}

resource "routeros_ip_dns_adlist" "blocklists" {
  for_each = toset(var.dns.adlists)

  url        = each.value
  ssl_verify = true
  depends_on = [routeros_ip_dns.this]
}

# --- NTP ---------------------------------------------------------------------
# VyOS: `service ntp server us.pool.ntp.org` plus allow-client for the RFC1918
# ranges, which made the router an NTP server for the LAN. The forced-NTP dstnat
# rules depend on server mode being on.

resource "routeros_system_ntp_client" "this" {
  enabled = true
  servers = var.ntp.servers
  mode    = var.ntp.client_mode
}

resource "routeros_system_ntp_server" "this" {
  enabled = var.ntp.server_mode

  depends_on = [routeros_system_ntp_client.this]
}
