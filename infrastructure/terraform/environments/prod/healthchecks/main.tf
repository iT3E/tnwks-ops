## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Thin root module: modules/healthchecks holds the resources, this file the
## values, and secrets.sops.yaml the API key.
##
## Off-site heartbeats: things at home ping healthchecks.io on a schedule, and
## healthchecks.io messages Telegram when the pings stop. It catches what
## in-cluster alerting can't report on: a power cut, a WAN outage, the desktop
## not booting back up, or Prometheus/Alertmanager being dead.
##
## Four checks, so a Telegram message says which layer failed:
##   - sce-rtr01 down (and the others with it): power or internet at home.
##   - homelab-wsl down: the house is online, the cluster or its alerting isn't.
##   - homelab-wsl-02 down: the second cluster (or WSL-02) isn't.
##   - only alert-delivery down: alerts fire but can't reach the War Room.
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

    # Alertmanager sends AlertDeliveryHeartbeat here every 2 minutes; the
    # alert only fires while Telegram notifications are getting through
    # (kube-prometheus-stack addons/alerts/heartbeats.yaml). Down while
    # homelab-wsl is up: War Room alerts are not being delivered.
    alert-delivery = {
      desc    = "Telegram delivery heartbeat from homelab-wsl Alertmanager (every 2m). Down while homelab-wsl is up: Alertmanager can't deliver to the War Room (bot token revoked or hijacked, bot removed, Telegram API unreachable)."
      tags    = ["homelab-wsl", "alertmanager"]
      timeout = 3 * 60
      grace   = 4 * 60
    }

    # homelab-wsl-02's Alertmanager sends its Watchdog here every 2 minutes,
    # like homelab-wsl's (kubernetes/clusters/wsl-02/apps/alertmanager).
    homelab-wsl-02 = {
      desc    = "Watchdog heartbeat from homelab-wsl-02 Alertmanager (every 2m). Down: its Prometheus, Alertmanager, the cluster, WSL-02, its WSL or its wifi is offline. Check sce-rtr01 and homelab-wsl to tell which."
      tags    = ["homelab-wsl-02"]
      timeout = 3 * 60
      grace   = 4 * 60
    }
    # homelab-wsl-02's AlertDeliveryHeartbeat: its Alertmanager is getting
    # Telegram messages through. Its own check, so homelab-wsl's delivery
    # can't hide a broken one here (and the other way round).
    alert-delivery-wsl-02 = {
      desc    = "Telegram delivery heartbeat from homelab-wsl-02 Alertmanager (every 2m). Down while homelab-wsl-02 is up: its Alertmanager can't deliver to the War Room."
      tags    = ["homelab-wsl-02", "alertmanager"]
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

module "healthchecks" {
  source = "../../../modules/healthchecks"

  # The one Telegram integration, added in the UI (docs/operations/external-monitoring.md).
  channel_kind = "telegram"
  checks       = local.checks
}
