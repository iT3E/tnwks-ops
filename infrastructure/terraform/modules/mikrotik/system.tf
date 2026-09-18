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
# VyOS shipped kern/warning to the k8s Vector aggregator over TCP:6001 with
# octet-counted framing. RouterOS remote logging is syslog over UDP/TCP; the
# framing choice lives on the Vector source, so the aggregator's socket for
# this router must accept the RouterOS format.

resource "routeros_system_logging_action" "remote" {
  count = var.syslog == null ? 0 : 1

  name            = "remote-vector"
  target          = "remote"
  remote          = var.syslog.remote
  remote_port     = var.syslog.remote_port
  remote_protocol = var.syslog.remote_protocol
  src_address     = var.syslog.src_address
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
  for_each = var.disabled_ip_services

  numbers  = each.key
  port     = each.value
  disabled = true
}

# --- Local accounts ---------------------------------------------------------

resource "routeros_system_user" "admins" {
  for_each = var.admin_users

  name    = each.key
  group   = each.value.group
  comment = each.value.comment
  address = each.value.address
}

resource "routeros_system_user_sshkeys" "admins" {
  for_each = {
    for pair in flatten([
      for user, cfg in var.admin_users : [
        for idx, key in coalesce(cfg.ssh_keys, []) : {
          key_id = "${user}/${idx}"
          user   = user
          pubkey = key
        }
      ]
    ]) : pair.key_id => pair
  }

  user       = routeros_system_user.admins[each.value.user].name
  key        = each.value.pubkey
  depends_on = [routeros_system_user.admins]
}
