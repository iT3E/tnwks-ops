# One-time imports for the cloudflare v4 -> v5 migration (PR #963). The zone
# settings that cloudflare_zone_settings_override managed in v4 are adopted as
# cloudflare_zone_setting / cloudflare_universal_ssl_setting without changing
# them. Safe to delete once applied.
#
# v5 has no schema for cloudflare_zone_settings_override, so its four state
# entries were removed with `terraform state rm` before the first v5 plan
# (a `removed` block can't decode them either). Auto Minify isn't carried
# over: Cloudflare retired it and it was already "off" everywhere.

import {
  for_each = toset(module.cf_domain_1.zone_setting_ids)
  to       = module.cf_domain_1.cloudflare_zone_setting.this[each.key]
  id       = "${module.cf_domain_1.zone_id}/${each.key}"
}

import {
  for_each = toset(module.cf_domain_2.zone_setting_ids)
  to       = module.cf_domain_2.cloudflare_zone_setting.this[each.key]
  id       = "${module.cf_domain_2.zone_id}/${each.key}"
}

import {
  for_each = toset(module.cf_domain_3.zone_setting_ids)
  to       = module.cf_domain_3.cloudflare_zone_setting.this[each.key]
  id       = "${module.cf_domain_3.zone_id}/${each.key}"
}

import {
  for_each = toset(module.cf_domain_4.zone_setting_ids)
  to       = module.cf_domain_4.cloudflare_zone_setting.this[each.key]
  id       = "${module.cf_domain_4.zone_id}/${each.key}"
}
import {
  to = module.cf_domain_1.cloudflare_universal_ssl_setting.this
  id = module.cf_domain_1.zone_id
}

import {
  to = module.cf_domain_2.cloudflare_universal_ssl_setting.this
  id = module.cf_domain_2.zone_id
}

import {
  to = module.cf_domain_3.cloudflare_universal_ssl_setting.this
  id = module.cf_domain_3.zone_id
}

import {
  to = module.cf_domain_4.cloudflare_universal_ssl_setting.this
  id = module.cf_domain_4.zone_id
}

