## ---------------------------------------------------------------------------------------------------------------------
## ACCESS POINTS
## Devices can't be created, only adopted. With allow_adoption the first apply
## adopts each AP once it shows up as pending, then names and configures it.
## ---------------------------------------------------------------------------------------------------------------------

resource "unifi_device" "ap" {
  for_each = var.access_points

  mac               = each.value.mac
  name              = each.value.name
  allow_adoption    = true
  forget_on_destroy = false

  config_network = {
    type      = "static"
    ip        = each.value.ip
    netmask   = var.management.netmask
    gateway   = var.management.gateway
    dns1      = var.management.dns
    dnssuffix = var.management.dnssuffix
  }

  # Matched to the device's radios by band; fields left out (null) keep the
  # controller's values, e.g. transmit power on a radio with no
  # tx_power_mode.
  radio_table = [
    {
      radio         = "ng"
      channel       = each.value.ng.channel
      ht            = each.value.ng.ht
      tx_power_mode = each.value.ng.tx_power_mode
      tx_power      = each.value.ng.tx_power
    },
    {
      radio         = "na"
      channel       = each.value.na.channel
      ht            = each.value.na.ht
      tx_power_mode = each.value.na.tx_power_mode
      tx_power      = each.value.na.tx_power
    },
  ]

  # Provision an AP only once the WLAN and site settings exist, or it comes up
  # with no SSID and the controller's default SSH credentials.
  depends_on = [unifi_wlan.main, unifi_setting.site]
}
