terraform {
  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
    }
  }
}

resource "cloudflare_zone" "zone" {
  zone       = var.domain
  account_id = var.account_id
  plan       = "free"
  type       = "full"
}

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
    minify {
      css  = "on"
      js   = "on"
      html = "on"
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

#####################################
##                                 ##
##               WAF               ##
##                                 ##
#####################################

resource "cloudflare_ruleset" "waf_custom_rules" {
  zone_id = cloudflare_zone.zone.id
  name    = "Zone custom WAF ruleset"
  kind    = "zone"
  phase   = "http_request_firewall_custom"

  dynamic "rules" {
    for_each = var.waf_custom_rules
    iterator = rule
    content {
      enabled     = rule.value.enabled
      description = rule.value.description
      expression  = rule.value.expression
      action      = rule.value.action

      dynamic "logging" {
        for_each = length(keys(rule.value.logging)) > 0 ? [true] : []
        content {
          enabled = lookup(rule.value.logging, "enabled", null)
        }
      }

      dynamic "action_parameters" {
        for_each = length(keys(rule.value.action_parameters)) > 0 ? [true] : []
        content {
          ruleset = lookup(rule.value.action_parameters, "ruleset", null)
        }
      }
    }
  }
}

#####################################
##                                 ##
##               DNS               ##
##                                 ##
#####################################

resource "cloudflare_record" "dns_records" {
  for_each = { for idx, dns_entry in var.dns_entries : idx => dns_entry }


  name     = each.value.name
  zone_id  = cloudflare_zone.zone.id
  value    = each.value.value
  priority = each.value.priority
  proxied  = contains(["A", "CNAME"], each.value.type) ? each.value.proxied : false
  type     = each.value.type
  ttl      = each.value.ttl
}
