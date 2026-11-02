# unifi

Terraform module for a UniFi Network site behind a third-party router: site
settings, one client VLAN, one WLAN, and the access points. Called by
`environments/prod/unifi`. Runbook, inventory and the network path from the
APs to the controller: [`docs/network/unifi.md`](../../../../docs/network/unifi.md).

| File | Manages |
| --- | --- |
| `settings.tf` | `unifi_setting`: country, firmware auto-upgrade, device SSH credentials and keys |
| `network.tf` | `unifi_network`: the client VLAN, as a vlan-only (third-party gateway) network |
| `wlan.tf` | `unifi_wlan`: the SSID, WPA2-PSK |
| `devices.tf` | `unifi_device`: adoption, name, static management address, pinned channels per AP |

Provider: [`ubiquiti-community/unifi`](https://registry.terraform.io/providers/ubiquiti-community/unifi/latest/docs),
the maintained successor of `paultyng/unifi`.

## Notes

- **Adoption.** `unifi_device` cannot create a device. With
  `allow_adoption = true`, creating the resource adopts a device that is
  already pending on the controller (factory-reset and informing to it). An AP
  that is not pending yet fails the apply; reset it and apply again.
- **`forget_on_destroy = false`.** Removing an AP from the config stops
  managing it; it stays adopted.
- **Radios** are matched by band (`ng`, `na`). Only the channel and width are
  set; everything else, including transmit power, stays with the controller.
- **Channel AI** (the nightly optimizer) would override pinned channels. It is
  off on a new self-hosted controller and this module does not turn it on.
- **Things Terraform can't do** (`task unifi:bootstrap` does them over the API): the
  first-run wizard, the admin accounts (including the one Terraform logs in
  with), and Override Inform Host.
- **WLAN first create** (provider 0.65): the apply reports "Provider produced
  inconsistent result after apply" although the WLAN is created as
  configured. `terraform untaint` it; the next plan is clean.
