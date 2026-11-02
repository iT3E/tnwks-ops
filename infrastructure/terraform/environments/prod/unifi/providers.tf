## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## The controller's API: the unifi-inform VIP from the desktop (the ingress is
## behind the Cognito login), the in-cluster Service from CI. Credentials
## are the local `terraform` admin created on the controller during bootstrap
## (docs/network/unifi.md): Terraform cannot create the account it logs in with.
## ---------------------------------------------------------------------------------------------------------------------

provider "unifi" {
  api_url  = coalesce(var.unifi_url, data.sops_file.secrets.data["unifi_url"])
  username = data.sops_file.secrets.data["unifi_username"]
  password = data.sops_file.secrets.data["unifi_password"]

  # Both endpoints serve the controller's own self-signed certificate.
  allow_insecure = true
}
