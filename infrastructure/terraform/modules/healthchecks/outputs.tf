## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

# The slugs pingers use (hc-ping.com/<ping key>/<slug>). The ping key itself is
# secret and lives in each pinger's SOPS file.
output "check_slugs" {
  description = "Check slugs; API v1 derives them from the names."
  value       = keys(healthchecksio_check.this)
}

output "channel_id" {
  description = "ID of the integration the checks notify."
  value       = data.healthchecksio_channel.this.id
}
