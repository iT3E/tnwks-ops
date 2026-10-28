output "zone_id" {
  value = cloudflare_zone.zone.id
}

output "zone" {
  value = cloudflare_zone.zone.name
}

output "zone_setting_ids" {
  description = "Setting ids managed by cloudflare_zone_setting.this (used by the root module's import blocks)."
  value       = keys(local.zone_settings)
}
