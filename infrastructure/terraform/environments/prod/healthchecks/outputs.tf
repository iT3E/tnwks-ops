## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

# The slugs pingers use (hc-ping.com/<ping key>/<slug>). The ping key itself is
# secret and lives in each pinger's SOPS file.
output "check_slugs" {
  description = "Check slugs; API v1 derives them from the names."
  value       = module.healthchecks.check_slugs
}
