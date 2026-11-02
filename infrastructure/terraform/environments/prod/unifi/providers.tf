## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## The controller's API through the WSL cluster's internal ingress. Credentials
## are the local `terraform` admin created on the controller during bootstrap
## (docs/network/unifi.md): Terraform cannot create the account it logs in with.
## ---------------------------------------------------------------------------------------------------------------------

provider "unifi" {
  api_url  = coalesce(var.unifi_url, data.sops_file.secrets.data["unifi_url"])
  username = data.sops_file.secrets.data["unifi_username"]
  password = data.sops_file.secrets.data["unifi_password"]

  # The ingress serves the *.internal.tnwks.us wildcard, so TLS verifies there.
  # Insecure stays on so the VIP fallback (the controller's self-signed cert)
  # works with the same config.
  allow_insecure = true
}
