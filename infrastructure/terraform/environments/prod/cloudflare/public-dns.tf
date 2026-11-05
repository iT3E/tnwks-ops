#####################################
##                                 ##
##   cf_domain_1: public app names ##
##                                 ##
#####################################

# Public apps reach the homelab through the cloudflared tunnel: each name is a
# proxied CNAME to ingress.<domain>, and that one is a CNAME to the tunnel
# (cloudflared then sends *.<domain> to ingress-nginx). external-dns made these
# from Ingress annotations until 2026-11; Terraform owns them now, so adding a
# public app is a line in local.cf1_public_hosts.
#
# Keys are the host names, except the one whose name is a secret
# (SECRET_PREFIX on the clusters): key "unlisted", name from SOPS.

locals {
  cf1_domain = data.sops_file.secrets.data["cf_domain_1"]
  cf1_public_hosts = toset([
    "analytics",
    "bitwarden",
    "echo-server",
    "faro",
    "flux-webhook",
    "hass",
    "invite",
    "jellyfin",
    "oauth2",
    "onboard",
    "recipes",
    "request",
    "requests",
    "sites",
    "unlisted",
  ])
}

resource "cloudflare_dns_record" "cf1_tunnel_ingress" {
  zone_id = module.cf_domain_1.zone_id
  name    = "ingress.${local.cf1_domain}"
  type    = "CNAME"
  content = "${data.sops_file.secrets.data["cf_domain_1_tunnel_id"]}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_dns_record" "cf1_public" {
  for_each = local.cf1_public_hosts

  zone_id = module.cf_domain_1.zone_id
  name    = "${each.key == "unlisted" ? data.sops_file.secrets.data["cf_domain_1_secret_host"] : each.key}.${local.cf1_domain}"
  type    = "CNAME"
  content = "ingress.${local.cf1_domain}"
  proxied = true
  ttl     = 1
}

# One-time adoption of the records external-dns created (2026-11-05). Safe to
# delete once applied.
import {
  to = cloudflare_dns_record.cf1_tunnel_ingress
  id = "${module.cf_domain_1.zone_id}/737d9f3bc51bc60caca469732e5614f3"
}

import {
  for_each = {
    "analytics"    = "3aa7f41a74808d3dcfbd21c5545e13a1"
    "bitwarden"    = "d6af5bc820117f716cc22959fdfb7a96"
    "echo-server"  = "19a21f606505636ce55317ced2e611fd"
    "faro"         = "2f905403f53f74a5340f2e813229c1c0"
    "flux-webhook" = "578ffabd9f413df921b8e43b9e9e5652"
    "hass"         = "b2759a929c8511c63b3da37e5ee5e743"
    "invite"       = "c3247c745577995c06035c35746879da"
    "jellyfin"     = "e93edda2e107a02a62a9077dcbd9da90"
    "oauth2"       = "e4d7be71fb1026e816fd35e6b0e0d699"
    "onboard"      = "4f6f76069c3e3805924fd753b03e0921"
    "recipes"      = "9fbb40d24f06c2d986fc63587e15e957"
    "request"      = "c7e3b7ac1b29047522ceb336d4d09862"
    "requests"     = "db4863b863249d61919567b3bf5fa218"
    "unlisted"     = "609bb1c12171122a29eb356fb2d50de0"
    "sites"        = "fa1e972a275aef391783f97cb236cdd8"
  }
  to = cloudflare_dns_record.cf1_public[each.key]
  id = "${module.cf_domain_1.zone_id}/${each.value}"
}
