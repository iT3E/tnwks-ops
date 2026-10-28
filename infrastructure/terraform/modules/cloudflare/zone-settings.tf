# Zone settings, one cloudflare_zone_setting per setting (v5 removed
# cloudflare_zone_settings_override). Values are what the zones already have;
# the root module imports the existing settings (see ../../environments/prod/
# cloudflare/zone-settings-import.tf), so nothing on Cloudflare changes.
locals {
  zone_settings = {
    # /ssl-tls
    ssl = "full"
    # /ssl-tls/edge-certificates
    always_use_https         = "on"
    min_tls_version          = "1.0"
    opportunistic_encryption = "on"
    tls_1_3                  = "zrt"
    automatic_https_rewrites = "on"
    # /firewall/settings
    browser_check  = "on"
    challenge_ttl  = 1800
    privacy_pass   = "on"
    security_level = "medium"
    # /speed/optimization
    brotli        = "on"
    rocket_loader = "on"
    # /caching/configuration
    always_online    = "off"
    development_mode = "off"
    # /network
    http3               = "on"
    "0rtt"              = "on"
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
    security_header = {
      strict_transport_security = {
        enabled            = false
        include_subdomains = false
        max_age            = 0
        nosniff            = false
        preload            = false
      }
    }
  }
}

resource "cloudflare_zone_setting" "this" {
  for_each = local.zone_settings

  zone_id    = cloudflare_zone.zone.id
  setting_id = each.key
  value      = each.value
}

# Universal SSL is its own endpoint in v5 (it was a field of the override).
resource "cloudflare_universal_ssl_setting" "this" {
  zone_id = cloudflare_zone.zone.id
  enabled = true
}
