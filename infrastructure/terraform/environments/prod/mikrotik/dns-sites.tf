## ---------------------------------------------------------------------------------------------------------------------
## Names served by homelab-wsl-02 (apps moved there, docs/wsl/multi-site.md). Each is more specific than the
## internal.tnwks.us wildcard (match_subdomain, 10.10.91.142 = homelab-wsl; locals.tf, generated) and points at
## WSL-02, whose LAN bridge forwards 80/443 to homelab-wsl-02's ingress-nginx-internal. match_subdomain = true on
## purpose: RouterOS checks those entries before plain names, so a plain record under the wildcard never answers
## (modules/mikrotik/dns.tf puts the most specific first). Hand-written: locals.tf is generated from the VyOS
## config. Short TTL, so a name moves back quickly.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  wsl02_lan_address = "10.10.91.112"

  # Fencing leases (docs/wsl/multi-site.md, "Fencing"): per warm app, the site
  # fenced from writing it, or "none". Set it to the dead site BEFORE an
  # unplanned promote (bin/site-failover --promote refuses otherwise), and
  # back to "none" once that site's database has been rebuilt as a replica.
  site_leases = {
    mealie      = "none"
    transcriber = "none"
    firefly     = "none"
  }

  site_dns_records = {
    wsl02_playwright_mcp = {
      name            = "playwright-mcp.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: playwright-mcp"
    }
    wsl02_transcriber = {
      name            = "transcriber.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: transcriber"
    }
    wsl02_changedetection = {
      name            = "changedetection.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: changedetection"
    }
    wsl02_immich = {
      name            = "immich.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: immich"
    }
    wsl02_immichframe = {
      name            = "immichframe.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: immichframe"
    }
    wsl02_kitchen_screensaver = {
      name            = "kitchen-screensaver.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02: kitchen-screensaver"
    }
  }
}
