## ---------------------------------------------------------------------------------------------------------------------
## Public app names behind the cloudflared tunnel: each one a proxied CNAME to ingress.<domain>, and that one a CNAME
## to the tunnel (cloudflared then sends *.<domain> to ingress-nginx). Terraform owns these since 2026-11; before, the
## clusters' external-dns made them from Ingress annotations. Adding a public app is one key in tunnel_public_hosts.
## ---------------------------------------------------------------------------------------------------------------------

resource "cloudflare_dns_record" "tunnel_ingress" {
  count = length(var.tunnel_public_hosts) > 0 ? 1 : 0

  zone_id = cloudflare_zone.zone.id
  name    = "ingress.${var.domain}"
  type    = "CNAME"
  content = "${var.tunnel_id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Keys are the host names, except where the name itself is a secret: then the key is a stand-in and
# tunnel_host_names maps it to the real name.
resource "cloudflare_dns_record" "tunnel_public" {
  for_each = var.tunnel_public_hosts

  zone_id = cloudflare_zone.zone.id
  name    = "${lookup(var.tunnel_host_names, each.key, each.key)}.${var.domain}"
  type    = "CNAME"
  content = "ingress.${var.domain}"
  proxied = true
  ttl     = 1
}
