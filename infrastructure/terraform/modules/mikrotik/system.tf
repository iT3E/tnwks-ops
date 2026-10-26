# System identity, clock and logging.
# Port of VyOS: system host-name / domain-name / time-zone / syslog.

resource "routeros_system_identity" "this" {
  name = var.identity
}

resource "routeros_system_clock" "this" {
  time_zone_name = var.timezone
}

resource "routeros_system_note" "this" {
  note          = <<-EOT
    Managed by Terraform: infrastructure/terraform/environments/prod/mikrotik
    Do not edit via WinBox/CLI. Changes are reverted on the next apply.
  EOT
  show_at_login = true
}

# --- Remote syslog -----------------------------------------------------------
# Syslog to monitoring/vector-aggregator (routeros_syslog source, :6001) via
# the WSL host's relays; see docs/wsl/lan-exposure.md. Use UDP: RouterOS's TCP
# syslog has no message framing.

resource "routeros_system_logging_action" "remote" {
  count = var.syslog == null ? 0 : 1

  # RouterOS 7.25: action names are letters and digits only.
  name            = "remotevector"
  target          = "remote"
  remote          = var.syslog.remote
  remote_port     = var.syslog.remote_port
  remote_protocol = var.syslog.remote_protocol
  src_address     = var.syslog.src_address

  remote_log_format  = var.syslog.log_format
  syslog_time_format = var.syslog.time_format
}

resource "routeros_system_logging" "remote" {
  for_each = var.syslog == null ? toset([]) : toset(var.syslog.topics)

  topics = [each.value]
  action = routeros_system_logging_action.remote[0].name
}

# --- Services ----------------------------------------------------------------
# VyOS exposed SSH only. Everything RouterOS enables by default is disabled.
# The provider requires `port` on every ip_service entry, even to disable it, so
# the map carries each service's default port.

resource "routeros_ip_service" "ssh" {
  numbers = "ssh"
  port    = var.ssh_port
}

resource "routeros_ip_service" "disabled" {
  # api-ssl stays on when mktxp is configured; see below.
  for_each = {
    for name, port in var.disabled_ip_services : name => port
    if !(name == "api-ssl" && local.monitoring_enabled)
  }

  numbers  = each.key
  port     = each.value
  disabled = true
}

# VyOS: `service ssh disable-password-authentication`. RouterOS 7.25 replaced
# always-allow-password-login with password-authentication, whose default
# (yes-if-no-key) already refuses passwords for any user with a key, which
# is what the admin account has. Provider v1.99.1 cannot set the new
# attribute, so only strong crypto is pinned here.
resource "routeros_ip_ssh_server" "this" {
  strong_crypto = true
}

# --- Local accounts ---------------------------------------------------------

locals {
  # Whether an admin is configured is not itself secret; unwrapping just the
  # boolean keeps count/for_each legal without exposing the username.
  admin_enabled = nonsensitive(var.admin_username != null)
}

resource "routeros_system_user" "admin" {
  count = local.admin_enabled ? 1 : 0

  name     = var.admin_username
  password = var.admin_password
  group    = "full"
  comment  = "Interactive admin, SSH key only (terraform)"

  lifecycle {
    precondition {
      condition     = var.admin_password != null
      error_message = "admin_password is required: a RouterOS user without one has an empty password."
    }
  }
}

resource "routeros_system_user_sshkeys" "admin" {
  for_each = local.admin_enabled ? { for idx, key in var.admin_ssh_keys : tostring(idx) => key } : {}

  user = routeros_system_user.admin[0].name
  key  = each.value
}

# --- Monitoring account (mktxp) ----------------------------------------------
# Read-only and API-only: the group grants `api` + `read` and nothing else, so
# the account cannot log in over SSH/Winbox/REST or change configuration. Plain
# `api` (8728) stays disabled; mktxp connects over api-ssl with the bootstrap
# certificate.

locals {
  monitoring_enabled = var.monitoring_username != null
}

resource "routeros_system_user_group" "monitoring" {
  count = local.monitoring_enabled ? 1 : 0

  name    = "monitoring"
  policy  = ["api", "read"]
  comment = "mktxp: read-only API (terraform)"
}

resource "routeros_system_user" "monitoring" {
  count = local.monitoring_enabled ? 1 : 0

  name     = var.monitoring_username
  password = var.monitoring_password
  group    = routeros_system_user_group.monitoring[0].name
  comment  = "mktxp exporter (terraform)"

  lifecycle {
    precondition {
      condition     = var.monitoring_password != null
      error_message = "monitoring_password is required when monitoring_username is set."
    }
  }
}

resource "routeros_ip_service" "api_ssl" {
  count = local.monitoring_enabled ? 1 : 0

  numbers     = "api-ssl"
  port        = 8729
  certificate = var.api_certificate
  disabled    = false
}
