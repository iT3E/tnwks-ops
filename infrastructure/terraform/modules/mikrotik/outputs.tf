output "identity" {
  description = "Configured RouterOS identity."
  value       = routeros_system_identity.this.name
}

output "bridge_name" {
  description = "LAN VLAN-filtering bridge."
  value       = routeros_interface_bridge.lan.name
}

output "vlan_interfaces" {
  description = "Map of zone name => RouterOS VLAN interface name."
  value       = { for k, v in routeros_interface_vlan.svi : k => v.name }
}

output "vlan_addresses" {
  description = "Map of zone name => SVI address."
  value       = { for k, v in routeros_ip_address.svi : k => v.address }
}

output "zone_interface_lists" {
  description = "Map of zone name => RouterOS interface-list backing that zone."
  value       = { for k, v in routeros_interface_list.zone : k => v.name }
}

output "firewall_rule_count" {
  description = "Forward-chain rules created by this module (state policy + per-pair accepts + per-pair drops)."
  value       = 3 + length(routeros_ip_firewall_filter.zone_accept) + length(routeros_ip_firewall_filter.zone_drop)
}

output "dhcp_servers" {
  description = "Map of zone name => DHCP server name."
  value       = { for k, v in routeros_ip_dhcp_server.this : k => v.name }
}

output "wireguard_public_keys" {
  description = "Public keys RouterOS derived for each WireGuard listener, for peer reconfiguration."
  value       = { for k, v in routeros_interface_wireguard.this : k => v.public_key }
}

output "unported_vyos_subsystems" {
  description = <<-EOT
    Explicit record of what this module intentionally does not reproduce, so a
    future reader does not assume the port is lossy by accident. See
    docs/mikrotik-vyos-port.md for the reasoning behind each.
  EOT
  value = [
    "podman containers (blocky/dnsdist/bind/haproxy x3/unifi/uisp/node-exporter/speedtest-exporter/cloudflare-ddns)",
    "udp-broadcast-relay for Sonos (1900) and mDNS (5353)",
    "system conntrack helper modules (ftp/h323/nfs/pptp/sip/sqlnet/tftp)",
    "config-management commit-revisions (RouterOS uses its own backup/rollback)",
    "kernel.pty.max sysctl and reboot-on-panic",
  ]
}
