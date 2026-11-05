# healthchecks

Terraform module for a healthchecks.io project's checks: simple
(timeout + grace) heartbeats that all notify one existing integration. Called
by `environments/prod/healthchecks`. Runbook, the pingers and what each check
means: [`docs/operations/external-monitoring.md`](../../../../docs/operations/external-monitoring.md).

| File        | Manages                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------- |
| `data.tf`   | `healthchecksio_channel`: looks up the integration (`var.channel_kind`, default `telegram`) |
| `checks.tf` | `healthchecksio_check`: one check per entry in `var.checks`                                 |

Provider: [`kristofferahl/healthchecksio`](https://registry.terraform.io/providers/kristofferahl/healthchecksio/latest/docs).
The root pins the version and configures the API key.

## Notes

- **Integrations and API keys** exist only in the UI. The lookup returns a
  null id when nothing matches, so a postcondition fails the plan with a
  message saying what to add.
- **Slugs.** The provider uses API v1, which sets a check's slug from its
  name. Pingers address checks as `https://hc-ping.com/<ping key>/<slug>`, so
  they can be configured before the apply, and renaming a check changes its
  ping URL.
- **Detection time** is timeout + grace after the last ping.
