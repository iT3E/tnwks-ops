## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## ---------------------------------------------------------------------------------------------------------------------

variable "repositories" {
  description = <<-EOT
    Repositories of the account, by name. The settings default to what GitHub
    gives a new repository, so an entry lists only where a repository differs.
    The optional parts (vulnerability_alerts, actions, workflow_permissions,
    variables, environments, rulesets) are only managed where they are set:
    null or empty leaves them alone. security_and_analysis and rulesets need a
    public repository on this account's plan. name defaults to the key; set it
    (from SOPS) where the real name must stay out of the code, under a
    neutral key: keys are state addresses and show in CI output.
  EOT
  type = map(object({
    name         = optional(string)
    visibility   = string
    description  = optional(string, "")
    homepage_url = optional(string, "")
    topics       = optional(list(string), [])

    has_issues      = optional(bool, true)
    has_wiki        = optional(bool, true)
    has_projects    = optional(bool, true)
    has_discussions = optional(bool, false)
    is_template     = optional(bool, false)
    archived        = optional(bool, false)

    allow_merge_commit          = optional(bool, true)
    allow_squash_merge          = optional(bool, true)
    allow_rebase_merge          = optional(bool, true)
    allow_auto_merge            = optional(bool, false)
    allow_update_branch         = optional(bool, false)
    delete_branch_on_merge      = optional(bool, false)
    web_commit_signoff_required = optional(bool, false)
    squash_merge_commit_title   = optional(string, "COMMIT_OR_PR_TITLE")
    squash_merge_commit_message = optional(string, "COMMIT_MESSAGES")
    merge_commit_title          = optional(string, "MERGE_MESSAGE")
    merge_commit_message        = optional(string, "PR_TITLE")

    # "enabled" or "disabled" each. Public repositories only. The provider
    # reads back only these two, so the other secret scanning settings stay
    # unmanaged.
    security_and_analysis = optional(object({
      secret_scanning                 = string
      secret_scanning_push_protection = string
    }))

    # Dependabot alerts (github_repository_vulnerability_alerts).
    vulnerability_alerts = optional(bool)

    # Settings > Actions > General: whether Actions run, and which actions.
    actions = optional(object({
      enabled              = bool
      allowed_actions      = string
      sha_pinning_required = optional(bool, false)
    }))

    # Settings > Actions > General > Workflow permissions (GITHUB_TOKEN).
    workflow_permissions = optional(object({
      default_workflow_permissions     = string
      can_approve_pull_request_reviews = bool
    }))

    # Actions variables by name. Values are public: never a secret.
    variables = optional(map(string), {})

    # Deployment environments by name. reviewer_users are numeric user IDs.
    # branch_patterns need deployment_branch_policy.custom_branch_policies.
    environments = optional(map(object({
      can_admins_bypass   = optional(bool, true)
      prevent_self_review = optional(bool, false)
      wait_timer          = optional(number, 0)
      reviewer_users      = optional(list(number), [])
      deployment_branch_policy = optional(object({
        protected_branches     = bool
        custom_branch_policies = bool
      }))
      branch_patterns = optional(list(string), [])
    })), {})

    # Branch rulesets by name. bypass_actors: RepositoryRole 5 is admin,
    # Integration is a GitHub App ID.
    rulesets = optional(map(object({
      target      = optional(string, "branch")
      enforcement = optional(string, "active")
      include     = list(string)
      exclude     = optional(list(string), [])
      bypass_actors = optional(list(object({
        actor_type  = string
        actor_id    = optional(number)
        bypass_mode = optional(string, "always")
      })), [])

      creation                = optional(bool, false)
      update                  = optional(bool, false)
      deletion                = optional(bool, false)
      non_fast_forward        = optional(bool, false)
      required_linear_history = optional(bool, false)
      required_signatures     = optional(bool, false)

      pull_request = optional(object({
        allowed_merge_methods             = optional(list(string), ["merge", "squash", "rebase"])
        required_approving_review_count   = optional(number, 0)
        dismiss_stale_reviews_on_push     = optional(bool, false)
        require_code_owner_review         = optional(bool, false)
        require_last_push_approval        = optional(bool, false)
        required_review_thread_resolution = optional(bool, false)
      }))

      # Check context => GitHub App ID it must come from (null: any source).
      required_status_checks = optional(object({
        checks                               = map(number)
        strict_required_status_checks_policy = optional(bool, false)
        do_not_enforce_on_create             = optional(bool, false)
      }))
    })), {})
  }))

  validation {
    condition = alltrue([
      for name, repo in var.repositories :
      repo.visibility == "public" || (repo.security_and_analysis == null && length(repo.rulesets) == 0)
    ])
    error_message = "security_and_analysis and rulesets need a public repository: on this account's plan the API refuses them for private ones."
  }
}
