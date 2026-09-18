## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Thin root module. Everything structural lives in modules/mikrotik; everything
## site-specific lives in terraform.tfvars (non-secret) and secrets.sops.yaml.
## ---------------------------------------------------------------------------------------------------------------------

module "mikrotik" {
  source = "../../../modules/mikrotik"

  identity = var.identity
  domain   = var.domain
  timezone = var.timezone

  wan_interface       = var.wan_interface
  lan_trunk_interface = var.lan_trunk_interface
  bridge_name         = var.bridge_name
  bridge_ports        = var.bridge_ports

  vlans         = var.vlans
  static_routes = var.static_routes

  address_lists = var.address_lists
  port_lists    = var.port_lists
  zone_policies = var.zone_policies

  input_rules    = var.input_rules
  input_drop_log = var.input_drop_log

  dstnat_rules             = var.dstnat_rules
  masquerade_out_interface = var.masquerade_out_interface

  dns    = var.dns
  ntp    = var.ntp
  syslog = var.syslog

  ssh_port             = var.ssh_port
  disabled_ip_services = var.disabled_ip_services

  # WireGuard private keys are injected from SOPS, never from terraform.tfvars.
  wireguard_interfaces = {
    for name, cfg in var.wireguard_interfaces : name => merge(cfg, {
      private_key = data.sops_file.secrets.data["wireguard_${name}_private_key"]
    })
  }

  admin_users = {
    for name, cfg in var.admin_users : name => cfg
  }
}
