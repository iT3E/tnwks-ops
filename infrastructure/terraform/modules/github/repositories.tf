## ---------------------------------------------------------------------------------------------------------------------
## REPOSITORIES
## One github_repository per entry in var.repositories.
##
## Never deleted by Terraform: prevent_destroy refuses any plan that would.
## archive_on_destroy (archive instead of delete, should the guard ever be
## lifted) is not set yet: GitHub never reports it, so an import can't fill it
## in and it would show as an in-place update of every repository. It comes in
## its own change after the first apply (docs/operations/github.md).
## No template, fork or source_* arguments: on an existing repository they
## force a replacement, which is a delete. The deprecated private,
## default_branch, vulnerability_alerts, pages and has_downloads arguments stay
## unset (visibility and github_repository_vulnerability_alerts replace two of
## them).
## ---------------------------------------------------------------------------------------------------------------------

resource "github_repository" "this" {
  for_each = var.repositories

  name         = each.key
  description  = each.value.description
  homepage_url = each.value.homepage_url
  visibility   = each.value.visibility
  topics       = each.value.topics

  has_issues      = each.value.has_issues
  has_wiki        = each.value.has_wiki
  has_projects    = each.value.has_projects
  has_discussions = each.value.has_discussions
  is_template     = each.value.is_template
  archived        = each.value.archived

  allow_merge_commit          = each.value.allow_merge_commit
  allow_squash_merge          = each.value.allow_squash_merge
  allow_rebase_merge          = each.value.allow_rebase_merge
  allow_auto_merge            = each.value.allow_auto_merge
  allow_update_branch         = each.value.allow_update_branch
  delete_branch_on_merge      = each.value.delete_branch_on_merge
  web_commit_signoff_required = each.value.web_commit_signoff_required
  squash_merge_commit_title   = each.value.squash_merge_commit_title
  squash_merge_commit_message = each.value.squash_merge_commit_message
  merge_commit_title          = each.value.merge_commit_title
  merge_commit_message        = each.value.merge_commit_message

  dynamic "security_and_analysis" {
    for_each = each.value.security_and_analysis == null ? [] : [each.value.security_and_analysis]
    content {
      secret_scanning {
        status = security_and_analysis.value.secret_scanning
      }
      secret_scanning_push_protection {
        status = security_and_analysis.value.secret_scanning_push_protection
      }
    }
  }

  lifecycle {
    prevent_destroy = true

    # Deprecated and unused by the provider (it no longer reads alerts here),
    # never sent to or read from GitHub; its default would otherwise show as
    # a change on every imported repository.
    ignore_changes = [ignore_vulnerability_alerts_during_read]
  }
}
