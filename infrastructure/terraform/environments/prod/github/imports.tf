## ---------------------------------------------------------------------------------------------------------------------
## IMPORTS
## Two things on tnwks-ops that existed and weren't managed yet. This file
## goes again once an apply has imported them (docs/operations/github.md,
## Adopting something that already exists).
## ---------------------------------------------------------------------------------------------------------------------

# The Flux webhook (ID from GET /repos/iT3E/tnwks-ops/hooks).
import {
  to = module.github.github_repository_webhook.this["tnwks-ops/flux"]
  id = "tnwks-ops/421708682"
}

# The openclaw-hooks environment's variables.
import {
  for_each = local.openclaw_hooks_variables
  to       = module.github.github_actions_environment_variable.this["tnwks-ops/openclaw-hooks/${each.key}"]
  id       = "tnwks-ops:openclaw-hooks:${each.key}"
}
