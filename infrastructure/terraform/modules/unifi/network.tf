## ---------------------------------------------------------------------------------------------------------------------
## NETWORKS
## A third-party-gateway (vlan-only) network: the controller only needs the VLAN
## to tag WLAN traffic with. Device management stays untagged on the switch
## port's native VLAN, the controller's built-in Default network.
## ---------------------------------------------------------------------------------------------------------------------

resource "unifi_network" "clients" {
  name                = var.client_network.name
  third_party_gateway = true
  vlan                = var.client_network.vlan
}
