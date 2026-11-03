## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Off-site heartbeats: things at home ping healthchecks.io on a schedule, and
## healthchecks.io messages Telegram when the pings stop. It catches what
## in-cluster alerting can't report on: a power cut, a WAN outage, the desktop
## not booting back up, or Prometheus/Alertmanager being dead.
##
## Two checks, so a Telegram message says which layer failed:
##   - sce-rtr01 down, homelab-wsl down: power or internet at home.
##   - only homelab-wsl down: the house is online, the cluster isn't.
##
## Pingers address a check by slug, https://hc-ping.com/<ping key>/<slug>, so
## they can be configured before this root is applied. The provider uses API v1,
## which sets the slug to the slugified name. Renaming a check therefore changes
## its ping URL; update the pinger in the same PR.
##
## Detection time is timeout + grace after the last ping: a check goes "late"
## at timeout and "down" (notifies) after the grace period on top.
## See docs/operations/external-monitoring.md.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  checks = {
    # Alertmanager sends the always-firing Watchdog alert here as a webhook
    # every 2 minutes (kubernetes/apps/monitoring/alertmanager). Silence
    # means Prometheus, Alertmanager, the cluster, the desktop or the WAN is
    # down.
    homelab-wsl = {
      desc    = "Watchdog heartbeat from homelab-wsl Alertmanager (every 2m). Down: Prometheus, Alertmanager, the cluster, the desktop or the house is offline. Check sce-rtr01 to tell which."
      tags    = ["homelab-wsl", "alertmanager"]
      timeout = 3 * 60
      grace   = 4 * 60
    }

    # The router's scheduler fetches its ping URL every minute
    # (modules/mikrotik heartbeat.tf). Silence means power or WAN is out.
    sce-rtr01 = {
      desc    = "Heartbeat from the RB5009 router's scheduler (every 1m). Down: power or internet at home is out."
      tags    = ["network", "router"]
      timeout = 2 * 60
      grace   = 3 * 60
    }
  }
}

resource "healthchecksio_check" "this" {
  for_each = local.checks

  name    = each.key
  desc    = each.value.desc
  tags    = each.value.tags
  timeout = each.value.timeout
  grace   = each.value.grace

  channels = [data.healthchecksio_channel.telegram.id]
}
