## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

output "access_points" {
  description = "Controller ID, name and management address of each AP."
  value = {
    # Over the resources, not var.access_points, so a targeted apply of one AP
    # (adoption, one reset at a time) still evaluates.
    for k, d in unifi_device.ap : k => {
      id   = d.id
      name = d.name
      ip   = var.access_points[k].ip
    }
  }
}

output "wlan_id" {
  description = "Controller ID of the WLAN."
  value       = unifi_wlan.main.id
}

output "client_network_id" {
  description = "Controller ID of the client network."
  value       = unifi_network.clients.id
}
