## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Contains any data blocks that will be used in multiple sections.
##
## ---------------------------------------------------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

data "tls_certificate" "tfc_certificate" {
  url = "https://app.terraform.io"
}
