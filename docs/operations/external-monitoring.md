# External monitoring (healthchecks.io)

Everything else in `monitoring/` runs on homelab-wsl, so it goes quiet exactly
when the house does: a power cut, an ISP outage, or the desktop not booting
back up after one sends no alert. Off-site heartbeats cover that gap. Things at
home ping [healthchecks.io](https://healthchecks.io) on a schedule, and
healthchecks.io messages Telegram through its own bot when the pings stop.

## Checks

| Check         | Pinged by                                                  | Every | Notifies after silence of | Down means                                                              |
| ------------- | ---------------------------------------------------------- | ----- | ------------------------- | ----------------------------------------------------------------------- |
| `homelab-wsl` | Alertmanager, the always-firing `Watchdog` alert (webhook) | 1-2m  | 3m + 4m grace             | Prometheus, Alertmanager, the cluster, the desktop or the house is down |
| `sce-rtr01`   | RB5009 scheduler `heartbeat` (`/tool fetch`)               | 1m    | 2m + 3m grace             | Power or internet at home is out                                        |

Read them together:

- **Both down:** power or WAN. Nothing to do remotely until it comes back.
- **Only `homelab-wsl` down:** the house is online and the cluster isn't. The
  desktop may not have booted, WSL or the Talos containers may not have
  started, or Prometheus/Alertmanager is broken. The VPN still works.
- Each check sends a second message when it recovers.

## Where things live

- Checks: `infrastructure/terraform/environments/prod/healthchecks`, state in
  Terraform Cloud `tnwks-healthchecks-prod` (execution mode local), applied
  from the desktop with `task healthchecks:plan` / `task healthchecks:apply`.
  `task healthchecks:status` lists each check's status and last ping.
- Cluster pinger: `kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml`,
  receiver `healthchecks`. The URL is `healthchecks_watchdog_url` in that app's
  `secrets.sops.yaml`.
- Router pinger: `infrastructure/terraform/modules/mikrotik/heartbeat.tf`. The
  ping key is `healthchecks_ping_key` in the MikroTik `secrets.sops.yaml`.

Pingers use slug URLs, `https://hc-ping.com/<ping key>/<slug>`. The slug is
the check's name (the provider uses API v1, which derives the slug from the
name), so a pinger can be configured before the check exists. Renaming a
check changes its URL. The ping key is per project and secret: anyone holding
it can ping, and so hide an outage, but can't read or change anything.

## One-time setup

Integrations and API keys can't be created through the API.

1. Sign up at healthchecks.io (free tier: 20 checks).
2. **Integrations > Telegram:** connect the HealthchecksBot to the chat that
   should get the messages. Terraform looks up the single Telegram integration
   (`data.healthchecksio_channel.telegram`), so keep exactly one.
3. **Settings > API Access:** create a read-write API key (Terraform) and a
   ping key (the pingers). Store them in SOPS:
   - `environments/prod/healthchecks/secrets.sops.yaml`: `api_key`
   - `environments/prod/mikrotik/secrets.sops.yaml`: `healthchecks_ping_key`
   - `kubernetes/apps/monitoring/alertmanager/app/secrets.sops.yaml`:
     `healthchecks_watchdog_url: https://hc-ping.com/<ping key>/homelab-wsl`
4. Create the `tnwks-healthchecks-prod` workspace (execution mode local) to
   match `modules/tfc/workspaces.tf`, then `task healthchecks:init` and
   `task healthchecks:apply`.
5. Merge. Flux rolls out the Alertmanager change (Reloader restarts it on the
   ConfigMap and Secret), and `task mikrotik:apply` (or the MikroTik CI apply)
   adds the router scheduler. Both checks leave the "new" state on their first
   ping.

## Testing

- Pings arriving: `task healthchecks:status`. `last_ping` for both checks
  should stay under two minutes old.
- Delivery: in the healthchecks.io UI, send a test notification from the
  Telegram integration.
- A real outage: disable the router's `heartbeat` scheduler for about 6
  minutes, then re-enable it (the next MikroTik apply would too). Expect a
  down message for `sce-rtr01` and a recovery message.
