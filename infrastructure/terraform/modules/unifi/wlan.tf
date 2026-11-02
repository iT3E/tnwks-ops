## ---------------------------------------------------------------------------------------------------------------------
## WLAN
## WPA2-PSK (AES/CCMP) with PMF off. Several clients are old IoT radios that
## WPA3 transition mode is known to upset.
## ---------------------------------------------------------------------------------------------------------------------

# The controller's built-in client group (rate limits); every WLAN needs one.
data "unifi_client_qos_rate" "default" {
  name = "Default"
}

resource "unifi_wlan" "main" {
  name          = var.wlan.ssid
  security      = "wpapsk"
  passphrase    = var.wlan.passphrase
  network_id    = unifi_network.clients.id
  user_group_id = data.unifi_client_qos_rate.default.id
  wlan_bands    = var.wlan.bands

  wpa = {
    mode = "wpa2"
    enc  = "ccmp"
  }
  wpa3 = {
    support    = false
    transition = false
  }
  pmf_mode             = "disabled"
  fast_roaming_enabled = false
  hide_ssid            = false
}
