# Home Assistant

Home Assistant runs on `homelab-wsl` from
`kubernetes/apps/home-automation/home-assistant/`. It's at
`https://hass.internal.tnwks.us`, with sign-in through the `tnwks-auth` Cognito
pool. Its recorder writes to the shared CNPG cluster (database
`home_assistant`).

## What's in git and what isn't

HA can't be fully declared in YAML. Since
[ADR-0010](https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md),
any integration that talks to a device or service is UI-only. Since 2026.8 the
HTTP server settings are too. So ownership is split:

| Git (`app/config/`, read-only in the UI)                                    | UI (`/config/.storage` on the PVC)                                     |
| --------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Core settings: name, location, units, time zone, URLs                       | Integrations: MQTT, Frigate, Telegram bot, Z-Wave JS, …                |
| Recorder, SSO (`auth_oidc`), dashboard resources                            | HTTP server: proxy trust, IP bans                                      |
| `packages/`: automations, helpers, templates                                | Entity / device / area / floor / label registries                      |
| `dashboards/`: YAML-mode dashboards (Security)                              | Users, the Overview dashboard, energy settings                         |
| Custom integration and card versions (`helmrelease.yaml`)                   | `automations.yaml`, `scripts.yaml`, `scenes.yaml` (the UI editors)     |

Back up the UI half with HA's own backups (Settings > System > Backups). Git
can't rebuild it.

Room and device tiles come from HA's built-in Overview dashboard, which is
built from area assignments. To get them, put devices in areas. They don't need
YAML.

## Layout

```
app/
├── helmrelease.yaml           # image, versions, env, mounts
├── kustomization.yaml         # one ConfigMap per directory below
├── secrets.sops.yaml          # HASS_LATITUDE / LONGITUDE / ELEVATION
├── config/
│   ├── configuration.yaml     # → /config/configuration.yaml
│   ├── packages/*.yaml        # → /config/packages/
│   └── dashboards/*.yaml      # → /config/dashboards/
└── scripts/prepare-config.sh  # init container: installs custom integrations + cards
```

Flux variable substitution is off for these ConfigMaps, so Jinja is never
mangled. Per-cluster values reach HA as container env vars, which the config
reads with `!env_var`.

## Making a change

1. Edit or add a file under `config/`. A **new** file must also be listed under
   `configMapGenerator` in `kustomization.yaml` (CI checks this).
2. Open a PR. The `Home Assistant` workflow runs HA's `check_config` against
   the pinned image and integration versions.
3. After merge, Flux updates the ConfigMap and reloader restarts the pod.

A PR that only edits existing files under `config/` doesn't need a Changie
fragment, because automation and dashboard tuning doesn't cut a release. A PR
that touches anything else (including a new config file, since that edits
`kustomization.yaml`) needs one as usual.

Renovate bumps the image and the three pinned add-ons (`hass-oidc-auth`, the
Frigate integration, the Advanced Camera Card). The same check runs on those
PRs, which catches HA's frequent renames of trigger and condition keys.

## First-time setup

These steps are needed once per fresh `/config` volume, in this order.
Everything goes through a port-forward until step 3 has made HA trust
ingress-nginx. Before that, HA answers every proxied request with `400`.

```bash
export KUBECONFIG=~/.kube/config-homelab-wsl
kubectl -n home-automation port-forward svc/home-assistant 8123:8123
```

1. **Onboarding.** Open `http://localhost:8123` and create the owner account.
   This is the local break-glass admin, so keep its password in 1Password.
   Location and units are YAML-owned and show as read-only.
2. **Ignore the SSO redirect for now.** If a login page sends you to Cognito,
   use `http://localhost:8123/?skip_oidc_redirect=true` instead. SSO only works
   on the real hostname.
3. **Proxy trust.** Settings > System > Network > HTTP server: turn on
   *Trust X-Forwarded-For*, set *Trusted proxies* to the pod CIDR
   (`10.42.0.0/16`, the cluster's `CLUSTER_CIDR`), and save. HA restarts and
   the port-forward drops. Restart the port-forward and **confirm the new
   settings within 5 minutes**, or HA reverts them. After that,
   `https://hass.internal.tnwks.us` works.
4. **SSO.** Sign in at `https://hass.internal.tnwks.us` with *Login with
   tnwks*. Cognito group `admins` maps to HA administrator and `viewers` to HA
   user. Anyone in neither group is rejected. HA creates the user and a person
   on first login.
5. **MQTT.** Settings > Devices & services > Add integration > MQTT. Use broker
   `mosquitto.home-automation.svc.cluster.local`, port `1883`, username
   `home-assistant`. The password is on the `home-assistant:` line of
   `sops -d kubernetes/apps/home-automation/mosquitto/app/secrets.sops.yaml`.
   Then reload automations (Tools > YAML > Automations). The night alert's
   MQTT trigger can't subscribe until the broker exists, so it needs the
   reload.
6. **Frigate.** Add integration > Frigate, URL
   `http://frigate.production.svc.cluster.local:5000`. This creates the
   `camera.frigate_*` entities the Security dashboard uses.
7. **Telegram.** Add integration > Telegram bot, platform *Broadcast*. Use the
   bot token from
   `sops -d kubernetes/apps/monitoring/alertmanager/app/secrets.sops.yaml`
   (`telegram_bot_token`), or a new bot from @BotFather. Then use the
   integration's ⋮ menu > *Add allowed chat ID* for the chat that should get
   alerts. Alerts go to the first allow-listed chat.
8. **Backups.** Settings > System > Backups: turn on automatic backups.

Optional: add Z-Wave JS with URL
`ws://zwave-js-ui.home-automation.svc.cluster.local:3000`, then assign devices
to areas so the Overview dashboard fills in.

## Night person alerts

`config/packages/frigate_night_alerts.yaml` sends one Telegram photo per
Frigate review, the first time the review becomes a person alert. It only
fires for exterior cameras, between 23:00 and 06:00, and only while
*Night person alerts* is on.

You can change the switch and the times on the Security dashboard's Alerts
view, but only until the next restart. The YAML `initial` values win, so make
lasting changes in the package. The switch defaulting to on is deliberate: a
restart should never quietly leave alerting off.

To test it, move the start and end times on the Alerts view so the window
covers now, then walk past an exterior camera. Restarting HA restores the real
window.

## Break-glass

- **SSO broken:** `https://hass.internal.tnwks.us/?skip_oidc_redirect=true`
  shows the local login, so use the owner account.
- **Proxy settings broken** (UI unreachable through ingress): use the
  port-forward above and fix them under Settings > System > Network.
