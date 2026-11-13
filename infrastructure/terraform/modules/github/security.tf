## ---------------------------------------------------------------------------------------------------------------------
## SECURITY
## Dependabot alerts, where a repository's entry sets vulnerability_alerts.
## Secret scanning and push protection are the security_and_analysis block on
## the repository itself (repositories.tf).
## ---------------------------------------------------------------------------------------------------------------------

resource "github_repository_vulnerability_alerts" "this" {
  for_each = { for repo, cfg in var.repositories : repo => cfg.vulnerability_alerts if cfg.vulnerability_alerts != null }

  repository = github_repository.this[each.key].name
  enabled    = each.value
}
