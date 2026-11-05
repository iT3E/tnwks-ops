# The tunnel records moved from the root (public-dns.tf, #1736) into modules/cloudflare/tunnel-dns.tf.
# Safe to delete once applied.

moved {
  from = cloudflare_dns_record.cf1_tunnel_ingress
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_ingress[0]
}

moved {
  from = cloudflare_dns_record.cf1_public["analytics"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["analytics"]
}

moved {
  from = cloudflare_dns_record.cf1_public["bitwarden"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["bitwarden"]
}

moved {
  from = cloudflare_dns_record.cf1_public["echo-server"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["echo-server"]
}

moved {
  from = cloudflare_dns_record.cf1_public["faro"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["faro"]
}

moved {
  from = cloudflare_dns_record.cf1_public["flux-webhook"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["flux-webhook"]
}

moved {
  from = cloudflare_dns_record.cf1_public["hass"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["hass"]
}

moved {
  from = cloudflare_dns_record.cf1_public["invite"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["invite"]
}

moved {
  from = cloudflare_dns_record.cf1_public["jellyfin"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["jellyfin"]
}

moved {
  from = cloudflare_dns_record.cf1_public["oauth2"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["oauth2"]
}

moved {
  from = cloudflare_dns_record.cf1_public["onboard"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["onboard"]
}

moved {
  from = cloudflare_dns_record.cf1_public["recipes"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["recipes"]
}

moved {
  from = cloudflare_dns_record.cf1_public["request"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["request"]
}

moved {
  from = cloudflare_dns_record.cf1_public["requests"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["requests"]
}

moved {
  from = cloudflare_dns_record.cf1_public["sites"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["sites"]
}

moved {
  from = cloudflare_dns_record.cf1_public["unlisted"]
  to   = module.cf_domain_1.cloudflare_dns_record.tunnel_public["unlisted"]
}
