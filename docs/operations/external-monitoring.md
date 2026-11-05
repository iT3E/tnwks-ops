# External monitoring (healthchecks.io)

Everything else in `monitoring/` runs on homelab-wsl, so it goes quiet exactly
when the house does: a power cut, an ISP outage, or the desktop not booting
back up after one sends no alert. Off-site heartbeats cover that gap. Things at
home ping [healthchecks.io](https://healthchecks.io) on a schedule, and
healthchecks.io messages Telegram through its own bot when the pings stop.

## Checks

| Check            | Pinged by                                                                    | Every | Notifies after silence of | Down means                                                                          |
| ---------------- | ---------------------------------------------------------------------------- | ----- | ------------------------- | ----------------------------------------------------------------------------------- |
| `sce-rtr01`      | RB5009 scheduler `heartbeat` (`/tool fetch`)                                 | 1m    | 2m + 3m grace             | Power or internet at home is out                                                    |
| `homelab-wsl`    | Alertmanager, the always-firing `Watchdog` alert (webhook)                   | 2m    | 3m + 4m grace             | Prometheus, Alertmanager, the cluster, the desktop or the house is down             |
| `alert-delivery` | Alertmanager, `AlertDeliveryHeartbeat`, firing while Telegram delivery works | 2m    | 3m + 4m grace             | Alerts fire but can't reach the War Room (bot token revoked, bot removed, API down) |

Each layer depends on the one above it, so read them top down:

- **`sce-rtr01` down** (the other two follow): power or WAN. Nothing to do
  remotely until it comes back.
- **`homelab-wsl` down, router up:** the house is online and the cluster isn't.
  The desktop may not have booted, WSL or the Talos containers may not have
  started, or Prometheus/Alertmanager is broken. The VPN still works.
  `alert-delivery` follows it down.
- **Only `alert-delivery` down:** monitoring works, but War Room messages
  aren't arriving. Check `kubectl -n monitoring logs alertmanager-0` for the
  Telegram error, and the bot (`telegram-bot-compromise` if it was hijacked).
- Each check sends a second message when it recovers.

The boundary is deliberate: off-site checks cover only what can silence the
monitoring stack itself (power, WAN, the cluster, the alerting pipeline and its
delivery). Everything inside is Prometheus' job, so apps get no checks here.

### How `alert-delivery` decides

`AlertDeliveryHeartbeat` (`kube-prometheus-stack/addons/alerts/heartbeats.yaml`)
stops firing when, over the last 15 minutes, Telegram notifications failed and
none got through. A notification counts as failed only after Alertmanager's
retries, and one failed message while others arrive (one that's too long, say)
stays in-stack as `AlertmanagerFailedToSendAlerts` in the War Room. It also
stops when Alertmanager's metrics are missing, so it fails safe. Blind spot:
if Telegram breaks while nothing needs sending, it goes unnoticed until the
next alert tries.

Alertmanager's metrics come from the `alertmanager` ServiceMonitor in
`kube-prometheus-stack/addons/servicemonitors`, relabeled to the job and container
the stock `alertmanager.rules` select.

## Where things live

- Checks: `infrastructure/terraform/modules/healthchecks` (resources), values
  in `infrastructure/terraform/environments/prod/healthchecks`, state in
  Terraform Cloud `tnwks-healthchecks-prod` (execution mode local, so runs
  execute on the runner, not in TFC). CI plans on the PR and applies on merge
  on the self-hosted `tnwks-ops-runner` (`healthchecks-{plan,apply}.yaml`,
  [`docs/network/ci.md`](../network/ci.md)). `task healthchecks:plan` /
  `task healthchecks:apply` from the desktop are for break-glass only.
  `task healthchecks:status` lists each check's status and last ping.
- Cluster pinger: `kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml`,
  receivers `healthchecks` and `healthchecks-delivery`. The URLs are
  `healthchecks_watchdog_url` and `healthchecks_delivery_url` in that app's
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
   (`data.healthchecksio_channel.this` in the module), so keep exactly one.
3. **Settings > API Access:** create a read-write API key (Terraform) and a
   ping key (the pingers). Store them in SOPS:
   - `environments/prod/healthchecks/secrets.sops.yaml`: `api_key`
   - `environments/prod/mikrotik/secrets.sops.yaml`: `healthchecks_ping_key`
   - `kubernetes/apps/monitoring/alertmanager/app/secrets.sops.yaml`:
     `healthchecks_watchdog_url: https://hc-ping.com/<ping key>/homelab-wsl`
     and `healthchecks_delivery_url: https://hc-ping.com/<ping key>/alert-delivery`
4. The `tnwks-healthchecks-prod` workspace (execution mode local) was
   created through the TFC API on 2026-11-03, matching
   `modules/tfc/workspaces.tf`; `aws-init/imports.tf` adopts it on the next
   `aws-init` apply. Then `task healthchecks:init` and
   `task healthchecks:apply`.
5. Merge. Flux rolls out the Alertmanager change (Reloader restarts it on the
   ConfigMap and Secret), and `mikrotik-apply.yaml` adds the router scheduler
   (`task mikrotik:apply` by hand if CI is down). All three checks leave the
   "new" state on their first ping.

## Testing

- Pings arriving: `task healthchecks:status`. `last_ping` for both checks
  should stay under two minutes old.
- Delivery: in the healthchecks.io UI, send a test notification from the
  Telegram integration.
- A real outage: disable the router's `heartbeat` scheduler for about 6
  minutes, then re-enable it (the next MikroTik apply would too). Expect a
  down message for `sce-rtr01` and a recovery message.
