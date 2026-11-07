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

  # The shared VIPs of the internal ingress (docs/wsl/multi-site.md, site-tunnels.tf): announced by both sites over
  # BGP, each preferred by the site that runs its apps, taken over by the other while it doesn't announce it. Either
  # site's ingress serves every app deployed on both (global Services), so a switchover needs no DNS change.
  vip_wsl    = "10.10.95.10"  # homelab-wsl first
  vip_wsl_02 = "10.10.95.200" # homelab-wsl-02 first

  # Fencing leases (docs/wsl/multi-site.md, "Fencing"): per warm app, the site
  # fenced from writing it, or "none". Set it to the dead site BEFORE an
  # unplanned promote (bin/site-failover --promote refuses otherwise), and
  # back to "none" once that site's database has been rebuilt as a replica.
  site_leases = {
    mealie         = "none"
    transcriber    = "none"
    firefly        = "none"
    home-assistant = "none"
  }

  site_dns_records = {
    vip_hass = {
      name            = "hass.internal.tnwks.us"
      address         = local.vip_wsl
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl first): home-assistant"
    }
    vip_mqtt = {
      name            = "mqtt.internal.tnwks.us"
      address         = "10.10.95.11"
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl first): mosquitto"
    }
    vip_firefly = {
      name            = "firefly.internal.tnwks.us"
      address         = local.vip_wsl
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl first): firefly"
    }
    wsl02_playwright_mcp = {
      name            = "playwright-mcp.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): playwright-mcp"
    }
    wsl02_transcriber = {
      name            = "transcriber.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): transcriber"
    }
    wsl02_changedetection = {
      name            = "changedetection.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): changedetection"
    }
    wsl02_immich = {
      name            = "immich.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): immich"
    }
    wsl02_immichframe = {
      name            = "immichframe.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): immichframe"
    }
    wsl02_kitchen_screensaver = {
      name            = "kitchen-screensaver.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): kitchen-screensaver"
    }
    wsl02_unifi = {
      name            = "unifi.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): unifi"
    }
    wsl02_jellyfin = {
      name            = "jellyfin.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): jellyfin"
    }
    wsl02_qbittorrent = {
      name            = "qbittorrent.internal.tnwks.us"
      address         = local.vip_wsl_02
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "shared VIP (homelab-wsl-02 first): qbittorrent"
    }
    # Site 2's own Grafana (grafana.internal.tnwks.us stays with homelab-wsl). Its
    # LAN address, not a shared VIP: the name must reach site 2's ingress only.
    wsl02_grafana = {
      name            = "grafana-02.internal.tnwks.us"
      address         = local.wsl02_lan_address
      type            = "A"
      ttl             = "5m"
      match_subdomain = true
      comment         = "homelab-wsl-02 only: grafana-02"
    }
  }
}
