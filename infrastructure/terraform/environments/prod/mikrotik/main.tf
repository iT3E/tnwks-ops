## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Thin root module. Everything structural lives in modules/mikrotik; everything
## site-specific is generated into locals.tf from the VyOS config, and secrets
## come from secrets.sops.yaml.
##
## No variables.tf / terraform.tfvars here on purpose: this repo's other root
## modules (environments/prod/aws, environments/prod/cloudflare,
## environments/bootstrap/aws-init) all inline their values and read secrets from
## SOPS. Same pattern, so `task mikrotik:plan` needs no -var-file.
## ---------------------------------------------------------------------------------------------------------------------

module "mikrotik" {
  source = "../../../modules/mikrotik"

  identity = local.identity
  domain   = local.domain
  timezone = local.timezone

  wan_interface       = local.wan_interface
  lan_trunk_interface = local.lan_trunk_interface
  bridge_name         = local.bridge_name
  bridge_ports        = local.bridge_ports

  vlans         = local.vlans
  static_routes = local.static_routes

  address_lists = local.address_lists
  port_lists    = local.port_lists
  zone_policies = local.zone_policies

  input_rules = local.input_rules

  # EdgeRouter Lite consolidation. zone-lan scopes router services (DNS, NTP,
  # DHCP) to internal VLANs so they can never be answered on the WAN, which is
  # what the ERL got wrong. See docs/edgerouter-discovery.md.
  lan_interface_lists = local.lan_interface_lists
  connection_tracking = local.connection_tracking

  dstnat_rules             = local.dstnat_rules
  masquerade_out_interface = local.masquerade_out_interface

  dns    = local.dns
  ntp    = local.ntp
  syslog = local.syslog

  # WireGuard private keys are injected from SOPS, never committed. The rest of
  # each listener (address, port, peers) is generated from the VyOS config.
  wireguard_interfaces = {
    for name, cfg in local.wireguard_interfaces : name => merge(cfg, {
      private_key = data.sops_file.secrets.data["wireguard_${name}_private_key"]
    })
  }

  # VyOS interpolated the admin username from ${SSH_VYOS_USERNAME}, so it stays a
  # secret here too; only the public key material is generated into locals.tf.
  admin_users = {
    (data.sops_file.secrets.data["admin_username"]) = {
      group    = "full"
      comment  = "Interactive admin, SSH key only"
      ssh_keys = local.admin_ssh_keys
    }
  }
}
