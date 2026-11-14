## ---------------------------------------------------------------------------------------------------------------------
## ACTIONS
## Per-repository GitHub Actions settings: whether Actions run and which
## actions they may use, the GITHUB_TOKEN's default permissions, and Actions
## variables. Each is managed only where the repository's entry sets it.
## Secrets are not managed here.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  actions_variables = merge([
    for repo, cfg in var.repositories : {
      for name, value in cfg.variables : "${repo}/${name}" => {
        repository = repo
        name       = name
        value      = value
      }
    }
  ]...)
}

resource "github_actions_repository_permissions" "this" {
  for_each = { for repo, cfg in var.repositories : repo => cfg.actions if cfg.actions != null }

  repository           = github_repository.this[each.key].name
  enabled              = each.value.enabled
  allowed_actions      = each.value.allowed_actions
  sha_pinning_required = each.value.sha_pinning_required
}

resource "github_workflow_repository_permissions" "this" {
  for_each = { for repo, cfg in var.repositories : repo => cfg.workflow_permissions if cfg.workflow_permissions != null }

  repository                       = github_repository.this[each.key].name
  default_workflow_permissions     = each.value.default_workflow_permissions
  can_approve_pull_request_reviews = each.value.can_approve_pull_request_reviews
}

resource "github_actions_variable" "this" {
  for_each = local.actions_variables

  repository    = github_repository.this[each.value.repository].name
  variable_name = each.value.name
  value         = each.value.value
}
