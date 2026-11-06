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
  hosturl  = coalesce(var.routeros_url, data.sops_file.secrets.data["routeros_url"])
  username = data.sops_file.secrets.data["routeros_username"]
  password = data.sops_file.secrets.data["routeros_password"]

  # The bootstrap script installs a self-signed cert. Flip to false only once a
  # CA-issued cert is in place (see the PKI Infrastructure project).
  insecure = true
}

# The same REST API, as plain JSON objects, for what the routeros provider
# can't write on this RouterOS (7.25): BGP connections. Its v1.99.1 always
# sends add-path-out, which 7.22+ removed (terraform-provider-routeros#1030,
# #1016). Back to routeros_routing_bgp_connection once that's fixed.
provider "restapi" {
  uri                  = "${trimsuffix(coalesce(var.routeros_url, data.sops_file.secrets.data["routeros_url"]), "/")}/rest"
  username             = data.sops_file.secrets.data["routeros_username"]
  password             = data.sops_file.secrets.data["routeros_password"]
  insecure             = true
  create_method        = "PUT"
  update_method        = "PATCH"
  destroy_method       = "DELETE"
  write_returns_object = true
  id_attribute         = ".id"
}
