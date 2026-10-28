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

  # v5: rules is a list attribute instead of repeated blocks.
  rules = [
    for rule in var.waf_custom_rules : {
      enabled     = rule.enabled
      description = rule.description
      expression  = rule.expression
      action      = rule.action
      logging     = length(keys(rule.logging)) > 0 ? { enabled = lookup(rule.logging, "enabled", null) } : null
      action_parameters = length(keys(rule.action_parameters)) > 0 ? {
        ruleset = lookup(rule.action_parameters, "ruleset", null)
      } : null
    }
  ]
}
