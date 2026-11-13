## ---------------------------------------------------------------------------------------------------------------------
## IMPORTS
## Everything in this root existed before it: the first apply adopts it and
## changes nothing (the plan reads "to import" only). Safe to delete after
## that apply. Anything added to main.tf later that already exists on GitHub
## needs its own import block here first, or the apply fails on "already
## exists".
## ---------------------------------------------------------------------------------------------------------------------

import {
  for_each = local.repositories
  to       = module.github.github_repository.this[each.key]
  id       = each.key
}

# The repositories named in repositories.sops.yaml. The import ID is the
# repository name and can't be sensitive, so nonsensitive() here, and only
# here. Terraform prints import IDs in its progress lines; what reaches CI's
# public log is in docs/operations/github.md (Public logs).
import {
  for_each = local.sops_repositories
  to       = module.github.github_repository.this[each.key]
  id       = nonsensitive(data.sops_file.repositories.data["${each.key}.name"])
}

# tnwks-ops: the main ruleset, its environments, Actions settings and variables.

import {
  to = module.github.github_repository_ruleset.this["tnwks-ops/main"]
  id = "tnwks-ops:25843046"
}

import {
  for_each = toset(["external-contributions", "openclaw-hooks", "prod"])
  to       = module.github.github_repository_environment.this["tnwks-ops/${each.key}"]
  id       = "tnwks-ops:${each.key}"
}

import {
  to = module.github.github_repository_environment_deployment_policy.this["tnwks-ops/openclaw-hooks/main"]
  id = "tnwks-ops:openclaw-hooks:64710859"
}

import {
  for_each = toset(["RELEASE_APP_CLIENT_ID", "UNIFI_TERRAFORM_ENABLED"])
  to       = module.github.github_actions_variable.this["tnwks-ops/${each.key}"]
  id       = "tnwks-ops:${each.key}"
}

import {
  to = module.github.github_repository_vulnerability_alerts.this["tnwks-ops"]
  id = "tnwks-ops"
}

import {
  to = module.github.github_actions_repository_permissions.this["tnwks-ops"]
  id = "tnwks-ops"
}

import {
  to = module.github.github_workflow_repository_permissions.this["tnwks-ops"]
  id = "tnwks-ops"
}
