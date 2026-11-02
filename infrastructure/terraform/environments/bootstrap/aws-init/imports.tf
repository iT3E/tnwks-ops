## ---------------------------------------------------------------------------------------------------------------------
## IMPORTS
## Resources that exist in Terraform Cloud before this root was next applied.
##
## tnwks-mikrotik-prod was created through the TFC API, matching
## modules/tfc/workspaces.tf exactly, because this root could not be applied at
## the time (the init user cannot read the IAM OIDC provider that the pending
## moved blocks touch). These blocks make the next apply adopt it rather than
## fail on "already exists". Safe to delete after that apply.
## ---------------------------------------------------------------------------------------------------------------------

import {
  to = module.tfc.tfe_workspace.tnwks-mikrotik-prod
  id = "ws-uhGTMyqHbqxZBfo7"
}

import {
  to = module.tfc.tfe_workspace_settings.tnwks-mikrotik-prod_workspace_settings
  id = "ws-uhGTMyqHbqxZBfo7"
}

# tnwks-unifi-prod: created through the TFC API on 2026-11-01 the same way,
# matching modules/tfc/workspaces.tf. Safe to delete after the next apply.
import {
  to = module.tfc.tfe_workspace.tnwks-unifi-prod
  id = "ws-aJ2gq7hV2FTTVH1N"
}

import {
  to = module.tfc.tfe_workspace_settings.tnwks-unifi-prod_workspace_settings
  id = "ws-aJ2gq7hV2FTTVH1N"
}
