resource "cloudflare_zone_settings_override" "cloudflare_settings" {
  zone_id = cloudflare_zone.zone.id
  settings {
    # /ssl-tls
    ssl = "full"
    # /ssl-tls/edge-certificates
    always_use_https         = "on"
    min_tls_version          = "1.0"
    opportunistic_encryption = "on"
    tls_1_3                  = "zrt"
    automatic_https_rewrites = "on"
    universal_ssl            = "on"
    # /firewall/settings
    browser_check  = "on"
    challenge_ttl  = 1800
    privacy_pass   = "on"
    security_level = "medium"
    # /speed/optimization
    brotli = "on"
    # Cloudflare deprecated Auto Minify in Aug 2024; the API still reports the
    # field but Cloudflare flipped it off out-of-band. Match reality so plans
    # don't keep trying to push it back on.
    minify {
      css  = "off"
      js   = "off"
      html = "off"
    }
    rocket_loader = "on"
    # /caching/configuration
    always_online    = "off"
    development_mode = "off"
    # /network
    http3               = "on"
    zero_rtt            = "on"
    ipv6                = "on"
    websockets          = "on"
    opportunistic_onion = "on"
    pseudo_ipv4         = "off"
    ip_geolocation      = "on"
    # /content-protection
    email_obfuscation   = "on"
    server_side_exclude = "on"
    hotlink_protection  = "off"
    # /workers
    security_header {
      enabled = false
    }
  }
}
