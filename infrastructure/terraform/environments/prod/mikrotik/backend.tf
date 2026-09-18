## ---------------------------------------------------------------------------------------------------------------------
## BACKEND
## Terraform Cloud remote backend. Like the Cloudflare root module, this one is
## NOT wired into CI: RouterOS is only reachable from inside the LAN, so plans
## and applies run locally via `task terraform:mikrotik:{plan,apply}`.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-mikrotik-prod"
    }
  }
  required_version = ">= 1.9"
}
