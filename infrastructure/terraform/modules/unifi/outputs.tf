## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

output "access_points" {
  description = "Controller ID, name and management address of each AP."
  value = {
    for k, ap in var.access_points : k => {
      id   = unifi_device.ap[k].id
      name = ap.name
      ip   = ap.ip
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
