## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## Cloudflare credentials are sourced from credentials.sops.yaml through an
## ephemeral resource: Terraform reads them at each run and never writes them
## to state. Keep provider credentials out of secrets.sops.yaml, whose data
## source feeds resources and is saved in state.
## ---------------------------------------------------------------------------------------------------------------------

ephemeral "sops_file" "credentials" {
  source_file = "credentials.sops.yaml"
}

provider "cloudflare" {
  email   = ephemeral.sops_file.credentials.data["cloudflare_email"]
  api_key = ephemeral.sops_file.credentials.data["cloudflare_api_key"]
}
