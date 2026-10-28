#####################################
##                                 ##
##               DNS               ##
##                                 ##
#####################################

resource "cloudflare_dns_record" "dns_records" {
  for_each = { for idx, dns_entry in var.dns_entries : idx => dns_entry }


  name     = each.value.name
  zone_id  = cloudflare_zone.zone.id
  content  = each.value.value
  priority = each.value.type == "MX" ? each.value.priority : null # v5 keeps null for non-MX, as v4 stored it
  proxied  = contains(["A", "CNAME"], each.value.type) ? each.value.proxied : false
  type     = each.value.type
  ttl      = each.value.ttl
}

# cloudflare v4 -> v5: cloudflare_record became cloudflare_dns_record. The
# provider migrates the state on move, so the records are not recreated.
moved {
  from = cloudflare_record.dns_records
  to   = cloudflare_dns_record.dns_records
}
