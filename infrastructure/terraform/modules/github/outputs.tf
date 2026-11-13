## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

output "repository_ids" {
  description = "Numeric repository IDs by name, for app installations and rulesets in later changes."
  value       = { for name, repo in github_repository.this : name => repo.repo_id }
}
