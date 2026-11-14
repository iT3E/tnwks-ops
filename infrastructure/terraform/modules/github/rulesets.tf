## ---------------------------------------------------------------------------------------------------------------------
## RULESETS
## Repository rulesets, one per entry in a repository's rulesets map.
##
## The provider has no argument for the pull_request rule's
## require_extra_approval_for_unattributed_changes (true on tnwks-ops' main
## ruleset, set in the UI). A plan that leaves the ruleset unchanged doesn't
## touch it, but an apply that updates the ruleset sends the rule without the
## field, and GitHub may reset it to false. Check it in the UI after such an
## apply (docs/operations/github.md).
## ---------------------------------------------------------------------------------------------------------------------

locals {
  rulesets = merge([
    for repo, cfg in var.repositories : {
      for name, ruleset in cfg.rulesets : "${repo}/${name}" => merge(ruleset, { repository = repo, name = name })
    }
  ]...)
}

resource "github_repository_ruleset" "this" {
  for_each = local.rulesets

  repository  = github_repository.this[each.value.repository].name
  name        = each.value.name
  target      = each.value.target
  enforcement = each.value.enforcement

  conditions {
    ref_name {
      include = each.value.include
      exclude = each.value.exclude
    }
  }

  dynamic "bypass_actors" {
    for_each = each.value.bypass_actors
    content {
      actor_type  = bypass_actors.value.actor_type
      actor_id    = bypass_actors.value.actor_id
      bypass_mode = bypass_actors.value.bypass_mode
    }
  }

  rules {
    creation                = each.value.creation
    update                  = each.value.update
    deletion                = each.value.deletion
    non_fast_forward        = each.value.non_fast_forward
    required_linear_history = each.value.required_linear_history
    required_signatures     = each.value.required_signatures

    dynamic "pull_request" {
      for_each = each.value.pull_request == null ? [] : [each.value.pull_request]
      content {
        allowed_merge_methods             = pull_request.value.allowed_merge_methods
        required_approving_review_count   = pull_request.value.required_approving_review_count
        dismiss_stale_reviews_on_push     = pull_request.value.dismiss_stale_reviews_on_push
        require_code_owner_review         = pull_request.value.require_code_owner_review
        require_last_push_approval        = pull_request.value.require_last_push_approval
        required_review_thread_resolution = pull_request.value.required_review_thread_resolution
      }
    }

    dynamic "required_status_checks" {
      for_each = each.value.required_status_checks == null ? [] : [each.value.required_status_checks]
      content {
        strict_required_status_checks_policy = required_status_checks.value.strict_required_status_checks_policy
        do_not_enforce_on_create             = required_status_checks.value.do_not_enforce_on_create

        dynamic "required_check" {
          for_each = required_status_checks.value.checks
          content {
            context        = required_check.key
            integration_id = required_check.value
          }
        }
      }
    }
  }
}
