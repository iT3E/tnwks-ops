## ---------------------------------------------------------------------------------------------------------------------
## DATA
## Integrations can't be created through the API: the one the checks notify is
## added once in the healthchecks.io UI (Integrations), then looked up here.
## ---------------------------------------------------------------------------------------------------------------------

data "healthchecksio_channel" "this" {
  kind = var.channel_kind

  # With no match the provider returns a null id instead of an error.
  lifecycle {
    postcondition {
      condition     = self.id != null
      error_message = "No ${var.channel_kind} integration in the healthchecks.io project. Add one under Integrations first."
    }
  }
}
