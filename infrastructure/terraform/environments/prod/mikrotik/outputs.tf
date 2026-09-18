## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

output "identity" {
  description = "RouterOS identity."
  value       = module.mikrotik.identity
}

output "vlan_interfaces" {
  description = "Zone name => RouterOS VLAN interface."
  value       = module.mikrotik.vlan_interfaces
}

output "vlan_addresses" {
  description = "Zone name => SVI address."
  value       = module.mikrotik.vlan_addresses
}

output "firewall_rule_count" {
  description = "Forward-chain rule count, for comparison against the VyOS ruleset inventory."
  value       = module.mikrotik.firewall_rule_count
}

output "wireguard_public_keys" {
  description = "Public keys to redistribute to WireGuard clients after cutover."
  value       = module.mikrotik.wireguard_public_keys
}

output "unported_vyos_subsystems" {
  description = "What the port deliberately leaves behind."
  value       = module.mikrotik.unported_vyos_subsystems
}
