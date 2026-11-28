## ---------------------------------------------------------------------------------------------------------------------
## DATA
## secrets.sops.yaml holds only the provider's API key: an ephemeral
## resource reads it at each run and never writes it to state.
## ---------------------------------------------------------------------------------------------------------------------

ephemeral "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}
