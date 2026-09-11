## ---------------------------------------------------------------------------------------------------------------------
## DATA
## SOPS-encrypted secrets are decrypted here and passed into the module.
## ---------------------------------------------------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

data "aws_caller_identity" "current" {}
