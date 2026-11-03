## ---------------------------------------------------------------------------------------------------------------------
## DATA
## ---------------------------------------------------------------------------------------------------------------------

data "sops_file" "secrets" {
  source_file = "secrets.sops.yaml"
}

# Integrations can't be created through the API: the Telegram one is added once
# in the healthchecks.io UI (Integrations > Telegram), then looked up here.
data "healthchecksio_channel" "telegram" {
  kind = "telegram"
}
