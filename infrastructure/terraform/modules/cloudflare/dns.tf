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
