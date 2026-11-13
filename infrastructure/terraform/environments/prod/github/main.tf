## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Thin root module: modules/github holds the resources, this file the values,
## and secrets.sops.yaml the token.
##
## The repositories of the iT3E account, as they were when this root was
## added: every value below was read from the API and imported as is
## (imports.tf). An entry lists only what differs from a new repository's
## defaults (modules/github/variables.tf). Private repositories have no
## security_and_analysis or rulesets: the account's plan doesn't offer them.
##
## This root manages the ruleset and the prod environment that gate its own
## apply. A change that locks CI out is undone with `task github:apply`
## (docs/operations/github.md).
## ---------------------------------------------------------------------------------------------------------------------

locals {
  # GitHub user ID of the account owner, the reviewer of external-contributions.
  owner_user_id = 26583558

  # GitHub App ID of GitHub Actions: required checks must come from it.
  github_actions_app_id = 15368

  # GitHub App that may push to main past the ruleset (a bypass actor of the
  # live ruleset).
  bypass_app_id = 5472969

  # Secret scanning and push protection, the two states public repositories
  # are in (tnwks-ops has scanning without push protection).
  scanning_on = {
    secret_scanning                 = "enabled"
    secret_scanning_push_protection = "enabled"
  }
  scanning_off = {
    secret_scanning                 = "disabled"
    secret_scanning_push_protection = "disabled"
  }

  repositories = {
    #####################################
    ##             public              ##
    #####################################

    tnwks-ops = {
      visibility   = "public"
      description  = "A mono repository for my home infrastructure and Kubernetes cluster which adheres to Infrastructure as Code (IaC) and GitOps practices where possible"
      homepage_url = "https://iT3E.github.io/tnwks-ops"
      security_and_analysis = merge(local.scanning_on, {
        secret_scanning_push_protection = "disabled"
      })

      vulnerability_alerts = true
      actions = {
        enabled         = true
        allowed_actions = "all"
      }
      workflow_permissions = {
        default_workflow_permissions     = "read"
        can_approve_pull_request_reviews = false
      }
      variables = {
        # The release GitHub App's client ID (public by design).
        RELEASE_APP_CLIENT_ID = "Iv23li4tvKlTdMwPMmJR"
        # Turns on the UniFi root in terraform.yaml (docs/network/unifi.md).
        UNIFI_TERRAFORM_ENABLED = "true"
      }

      environments = {
        # Approval gate for Terraform plans on other people's PRs
        # (terraform.yaml, plan-approval).
        external-contributions = {
          reviewer_users = [local.owner_user_id]
        }
        # Deployments from main only.
        openclaw-hooks = {
          deployment_branch_policy = {
            protected_branches     = false
            custom_branch_policies = true
          }
          branch_patterns = ["main"]
        }
        # Terraform applies (terraform.yaml, apply): protected branches only.
        prod = {
          deployment_branch_policy = {
            protected_branches     = true
            custom_branch_policies = false
          }
        }
      }

      rulesets = {
        # The required checks and squash-only merges of AGENTS.md. Admins
        # (repository role 5) and one GitHub App bypass it. Its pull_request
        # rule also has require_extra_approval_for_unattributed_changes =
        # true, which the provider can't express (modules/github/rulesets.tf).
        main = {
          include = ["~DEFAULT_BRANCH"]
          bypass_actors = [
            { actor_type = "RepositoryRole", actor_id = 5 },
            { actor_type = "Integration", actor_id = local.bypass_app_id },
          ]
          deletion         = true
          non_fast_forward = true
          pull_request = {
            allowed_merge_methods = ["squash"]
          }
          required_status_checks = {
            checks = {
              "Build Success"             = local.github_actions_app_id
              "betterleaks / betterleaks" = local.github_actions_app_id
              "privacy / privacy"         = local.github_actions_app_id
            }
          }
        }
      }
    }

    github-workflows = {
      visibility             = "public"
      description            = "Reusable GitHub Actions workflows and Renovate presets for app repos"
      topics                 = ["github-actions", "renovate-config", "reusable-workflows"]
      has_wiki               = false
      has_projects           = false
      allow_merge_commit     = false
      allow_rebase_merge     = false
      delete_branch_on_merge = true
      security_and_analysis  = local.scanning_on
    }

    app-repo-template = {
      visibility             = "public"
      description            = "Copier template for app repos; their CI runs from iT3E/github-workflows"
      topics                 = ["copier-template", "github-actions"]
      has_wiki               = false
      is_template            = true
      allow_merge_commit     = false
      allow_rebase_merge     = false
      delete_branch_on_merge = true
      security_and_analysis  = local.scanning_on
    }

    tnwks-model-bridge = {
      visibility            = "public"
      description           = "The model bridge of the tnwks agent platform: the model gateway reaches the Claude plan through the Claude Agent SDK"
      has_wiki              = false
      security_and_analysis = local.scanning_off
    }

    tnwks-workflow-agents = {
      visibility            = "public"
      description           = "The workflow agents of the tnwks agent platform, with the run receiver and the run job"
      has_wiki              = false
      security_and_analysis = local.scanning_off
    }

    hass-config = {
      visibility            = "public"
      security_and_analysis = local.scanning_off
    }

    vyos-config = {
      visibility            = "public"
      security_and_analysis = local.scanning_off
    }

    nixos-dotfiles = {
      visibility            = "public"
      description           = "NixOS Dotfiles"
      security_and_analysis = local.scanning_on
    }

    mac-dotfiles = {
      visibility            = "public"
      security_and_analysis = local.scanning_on
    }

    dotfiles = {
      visibility            = "public"
      security_and_analysis = local.scanning_off
    }

    angular-sandbox = {
      visibility            = "public"
      security_and_analysis = local.scanning_on
    }

    bookbot = {
      visibility            = "public"
      security_and_analysis = local.scanning_on
    }

    bootdev-astroids = {
      visibility            = "public"
      security_and_analysis = local.scanning_on
    }

    flask-blog = {
      visibility            = "public"
      security_and_analysis = local.scanning_off
    }

    #####################################
    ##            private              ##
    #####################################
    # Only those already named elsewhere in this repo, outside the paths
    # .github/privacy-allow exempts. The others wait on the owner
    # (docs/operations/github.md, later phases).

    tnwks-agent-image = {
      visibility  = "private"
      description = "The derived image of the personal agents on the tnwks agent platform"
      has_wiki    = false
    }

    tnwks-coe = {
      visibility = "private"
      has_wiki   = false
    }

    tnwks-finance-sync = {
      visibility  = "private"
      description = "Simplifi to Firefly III sync and Amazon order enrichment jobs (container image)"
      has_wiki    = false
    }

    tnwks-packing-api = {
      visibility  = "private"
      description = "Tiny Go API backing the editable, synced Van Trip packing list (sites.tnwks.us)"
      has_wiki    = false
    }

    tnwks-sites = {
      visibility = "private"
      has_wiki   = false
    }

    tnwks-transcriber = {
      visibility  = "private"
      description = "Frigate speech to diarized transcripts (WhisperX on CPU) in Postgres"
      has_wiki    = false
    }

    tnwks-transcriber-web = {
      visibility  = "private"
      description = "Frontend for tnwks-transcriber: transcripts, audio, speaker labeling and summaries"
      has_wiki    = false
    }
  }
}

module "github" {
  source = "../../../modules/github"

  repositories = local.repositories
}
