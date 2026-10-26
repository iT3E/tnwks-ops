## ---------------------------------------------------------------------------------------------------------------------
## DYNAMIC DNS
##
## RouterOS has no built-in Cloudflare DDNS client. The only native
## option is `/ip cloud` (MikroTik's own DDNS), which gives you
## <serial>.sn.mynetname.net rather than your own hostname. So your own hostname
## means a script on the router plus a scheduler entry to run it.
##
## The script SOURCE lives in this repo (templates/ddns-*.rsc) and Terraform
## pushes it into /system/script. It is not a file on disk on the router and it
## is not a container; RouterOS stores the script body in its own config. That
## means:
##   - `terraform apply` is how you change it, WinBox edits get overwritten
##   - it survives reboots, and runs with no dependency on any other host
##   - the credential is stored on the router, readable by anyone with
##     sufficient RouterOS rights (see the security note below)
##
## Why this matters more than it looks: WireGuard client configs use the DDNS
## hostname as their endpoint. If this stops updating, remote VPN access breaks
## the next time the WAN address changes. Treat it as production, not a nicety.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  # The template reads the WAN DHCP lease rather than calling an external "what
  # is my IP" service, which would be another dependency in the path.
  ddns_script = var.ddns == null ? null : templatefile("${path.module}/templates/ddns-cloudflare.rsc", {
    zone_id       = var.ddns.zone_id
    record_id     = var.ddns.record_id
    record_name   = "${var.ddns_host}.${var.ddns_domain}"
    ttl           = var.ddns.ttl
    credential    = var.ddns_credential
    wan_interface = var.wan_interface
  })
}

resource "routeros_system_script" "ddns" {
  count = var.ddns != null ? 1 : 0

  name   = "ddns-update"
  source = local.ddns_script

  lifecycle {
    precondition {
      condition     = var.ddns_host != null && var.ddns_domain != null && var.ddns_credential != null
      error_message = "ddns requires ddns_host, ddns_domain and ddns_credential."
    }
  }

  # read  - read the DHCP client lease
  # write - set the :global that caches the last successful address
  # test  - /tool fetch lives under the test policy
  #
  # Deliberately NOT granting "policy": some community scripts include it, but it
  # confers user-management rights and is only needed to read globals created by
  # a different user. The scheduler owns this global, so it is unnecessary.
  policy = ["read", "write", "test"]

  comment = "Dynamic DNS via Cloudflare (terraform)"
}

resource "routeros_system_scheduler" "ddns" {
  count = var.ddns != null ? 1 : 0

  name     = "ddns-update"
  on_event = routeros_system_script.ddns[0].name
  interval = var.ddns.interval
  policy   = ["read", "write", "test"]

  comment = "Runs ddns-update every ${var.ddns.interval} (terraform)"
}
