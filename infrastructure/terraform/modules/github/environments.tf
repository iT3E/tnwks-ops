## ---------------------------------------------------------------------------------------------------------------------
## ENVIRONMENTS
## Deployment environments and their branch policies, from each repository's
## environments map. Environment secrets and variables are not managed here.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  environments = merge([
    for repo, cfg in var.repositories : {
      for name, env in cfg.environments : "${repo}/${name}" => merge(env, { repository = repo, environment = name })
    }
  ]...)

  # One policy per branch pattern of an environment.
  environment_branch_policies = merge([
    for key, env in local.environments : {
      for pattern in env.branch_patterns : "${key}/${pattern}" => {
        environment_key = key
        branch_pattern  = pattern
      }
    }
  ]...)
}

resource "github_repository_environment" "this" {
  for_each = local.environments

  repository          = github_repository.this[each.value.repository].name
  environment         = each.value.environment
  can_admins_bypass   = each.value.can_admins_bypass
  prevent_self_review = each.value.prevent_self_review
  wait_timer          = each.value.wait_timer

  dynamic "reviewers" {
    for_each = length(each.value.reviewer_users) == 0 ? [] : [each.value.reviewer_users]
    content {
      users = reviewers.value
    }
  }

  dynamic "deployment_branch_policy" {
    for_each = each.value.deployment_branch_policy == null ? [] : [each.value.deployment_branch_policy]
    content {
      protected_branches     = deployment_branch_policy.value.protected_branches
      custom_branch_policies = deployment_branch_policy.value.custom_branch_policies
    }
  }
}

resource "github_repository_environment_deployment_policy" "this" {
  for_each = local.environment_branch_policies

  repository     = github_repository_environment.this[each.value.environment_key].repository
  environment    = github_repository_environment.this[each.value.environment_key].environment
  branch_pattern = each.value.branch_pattern
}
