## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## RouterOS REST API over HTTPS. Credentials come from secrets.sops.yaml.
##
## The router must already have: an IP the workstation can reach, the REST API
## (`/ip/service enable www-ssl`) up with a certificate, and the Terraform
## service account created. Those are the bootstrap steps in
## infrastructure/mikrotik/bootstrap/ — Terraform cannot create the credentials
## it authenticates with.
## ---------------------------------------------------------------------------------------------------------------------

provider "routeros" {
  hosturl  = data.sops_file.secrets.data["routeros_url"]
  username = data.sops_file.secrets.data["routeros_username"]
  password = data.sops_file.secrets.data["routeros_password"]

  # The bootstrap script installs a self-signed cert. Flip to false only once a
  # CA-issued cert is in place (see the PKI Infrastructure project).
  insecure = true
}
