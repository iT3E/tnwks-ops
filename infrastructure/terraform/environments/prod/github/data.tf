## ---------------------------------------------------------------------------------------------------------------------
## DATA
## secrets.sops.yaml holds the token; repositories.sops.yaml the names and
## descriptions of the private repositories that main.tf lists under neutral
## keys. Two files, so the repositories could be committed before the owner
## created the token file. tnwks-ops.sops.yaml holds the tnwks-ops values
## that stay out of the code: the Flux webhook's URL and the openclaw-hooks
## environment's variables. Those three files are masked in CI's log
## (mask_sops); unmasked.sops.yaml holds values too short to mask, which CI
## decrypts but doesn't mask (docs/operations/github.md, The mask).
## ---------------------------------------------------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

data "sops_file" "repositories" {
  source_file = "repositories.sops.yaml"
}

data "sops_file" "tnwks_ops" {
  source_file = "tnwks-ops.sops.yaml"
}

data "sops_file" "unmasked" {
  source_file = "unmasked.sops.yaml"
}
