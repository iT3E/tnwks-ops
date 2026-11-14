## ---------------------------------------------------------------------------------------------------------------------
## WEBHOOKS
## Repository webhooks, from each repository's webhooks map. Created with the
## entry's settings, never updated: see the lifecycle block.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  webhooks = merge([
    for repo, cfg in var.repositories : {
      for name, hook in cfg.webhooks : "${repo}/${name}" => merge(hook, { repository = repo })
    }
  ]...)
}

resource "github_repository_webhook" "this" {
  for_each = local.webhooks

  repository = github_repository.this[each.value.repository].name
  events     = each.value.events
  active     = each.value.active

  configuration {
    url          = each.value.url
    content_type = each.value.content_type
    insecure_ssl = each.value.insecure_ssl
  }

  lifecycle {
    # The secret belongs to the receiver, never to this module. GitHub returns
    # it as "********", which an import stores in the state, so without this
    # every plan updates the secret to null. Ignoring only
    # configuration[0].secret isn't enough: on any update the provider sends
    # the whole configuration, secret included (the state's "********"), and
    # GitHub's REST docs say an update without the same secret sets a new
    # one or removes it. So every argument an update would send is ignored,
    # and Terraform never updates a webhook: it creates one (without a
    # secret) or adopts one, and plans a create if it disappears. A change to
    # a webhook is made on GitHub, and the entry updated to match
    # (docs/operations/github.md).
    ignore_changes = [configuration, events, active]
  }
}
