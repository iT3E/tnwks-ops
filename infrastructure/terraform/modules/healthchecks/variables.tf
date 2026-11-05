## ---------------------------------------------------------------------------------------------------------------------
## VARIABLES
## ---------------------------------------------------------------------------------------------------------------------

variable "channel_kind" {
  description = "Kind of the integration every check notifies (telegram, email, ...). It has to exist in the project already: integrations can only be added in the healthchecks.io UI."
  type        = string
  default     = "telegram"
}

variable "checks" {
  description = "Checks by name. The provider uses API v1, which derives the slug (hc-ping.com/<ping key>/<slug>) from the name, so renaming a check changes its ping URL. timeout and grace are in seconds: a check goes late at timeout after the last ping and down (notifies) after grace on top."
  type = map(object({
    desc    = string
    tags    = optional(list(string), [])
    timeout = number
    grace   = number
  }))

  validation {
    condition     = alltrue([for c in values(var.checks) : c.timeout >= 60 && c.grace >= 60])
    error_message = "healthchecks.io needs timeout and grace of at least 60 seconds."
  }
}
