## ---------------------------------------------------------------------------------------------------------------------
## Names served by homelab-wsl-02 (apps moved there, docs/wsl/multi-site.md). Each is more specific than the
## internal.tnwks.us wildcard (match_subdomain, 10.10.91.142 = homelab-wsl; locals.tf, generated) and points at
## WSL-02, whose LAN bridge forwards 80/443 to homelab-wsl-02's ingress-nginx-internal. Hand-written: locals.tf is
## generated from the VyOS config. Short TTL, so a name moves back quickly.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  wsl02_lan_address = "10.10.91.112"

  site_dns_records = {
    wsl02_playwright_mcp = {
      name            = "playwright-mcp.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = false
      comment         = "homelab-wsl-02: playwright-mcp"
    }
  }
}
