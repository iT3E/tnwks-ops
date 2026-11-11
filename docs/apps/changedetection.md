# changedetection.io

Watches web pages and sends an alert when they change, at
`https://changedetection.internal.tnwks.us` (LAN and admin VPN only,
`nginx-internal`). Today it watches the AI labs' misalignment-report pages.
An alert posts the new entry titles straight to the War Room on Telegram, with
no LLM in the path.

Manifests: `kubernetes/apps/production/changedetection/`, image
`ghcr.io/dgtlmoon/changedetection.io` in `app/helmrelease.yaml`. It runs on
homelab-wsl-02 (`CHANGEDETECTION_REPLICAS` in each site's cluster settings;
switching sites is in [multi-site.md](../wsl/multi-site.md)). Upstream:
[changedetection.io](https://changedetection.io),
[GitHub](https://github.com/dgtlmoon/changedetection.io) (Apache-2.0).

## Pieces

| Piece | Where |
| --- | --- |
| App | StatefulSet `changedetection`, port 5000 |
| Data | PVC `config-changedetection-0` (`/datastore`: `watch.json`, settings, history), backed up nightly by kopiur (`kopiur-system/kopiur/policies/changedetection.yaml`) |
| UI | Ingress `changedetection`, behind oauth2-proxy |
| API | Ingress `changedetection-api`, path `/api/v1` only, no oauth2-proxy |

Watches, notification URLs and the API key live in the datastore, not in git.
They are app data, like Mealie's recipes, and the kopiur backup is how they
come back.

## Login

The UI is behind tnwks-auth (Cognito) through oauth2-proxy, members of
`admins` only (`auth-url ...?allowed_groups=admins`). changedetection's own
password is off, so oauth2-proxy is the only gate on the UI. homelab-wsl-02 has
no oauth2-proxy of its own and asks homelab-wsl's (`OAUTH2_PROXY_URL`), the same
as transcriber and UniFi.

`/api/v1` is a separate Ingress without oauth2-proxy, because scripts and the
agent manage watches through the REST API and can't do a browser login. The API
checks its own `x-api-key` on every call (Settings > API, "API access token
security check" on) and answers 403 without it. The key is in the datastore;
the operator's copy is outside the repo.

Residual risk: a pod that reaches the Service directly gets the UI without
logging in. No NetworkPolicy limits that today.

## Alerts

Each watch sends its alert through Apprise (the notification library built into
changedetection) to Telegram, as the Alertmanager bot, into the War Room (the
same group and bot as the cluster alerts). The notification URL is
`tgram://<bot token>/<War Room chat ID>/`. The bot token is
`telegram_bot_token` in `kubernetes/apps/monitoring/alertmanager/app/secrets.sops.yaml`,
and the chat ID is `chat_id` in that app's `config/alertmanager.yml`.

The message is changedetection's own text, with no LLM in between:

```
title: 👁️ {{watch_title}}
body:
{{watch_title}} changed
{{watch_url}}

New:
{{diff_added}}
```

So an alert is the list of new entry titles and the page to open, and costs no
tokens. Two watches that change in the same check send two messages.

The bot token sits in clear text in each watch's notification URL in the
datastore (and so in its backups). Rotating the Alertmanager bot token means
updating every watch's notification URL as well as the SOPS Secret.

## Watches

Every watch checks every 6 hours, keeps only the list of entry titles (a CSS or
XPath filter), sorts and de-duplicates the lines, and alerts on added lines
only. So a reordered list or an edited date doesn't alert, and a removed post
doesn't either.

| Watch | URL | Filter |
| --- | --- | --- |
| OpenAI misalignment reports | `https://alignment.openai.com/misalignment-reports/` | `a.report-title` |
| Anthropic Alignment Science blog | `https://alignment.anthropic.com/` | `a.note h3` |
| Anthropic research | `https://www.anthropic.com/research` | `xpath://a[starts-with(@href,"/research/")]` |
| METR blog | `https://metr.org/blog/` | `.blog-post-card .card-title` |

Every new title alerts, whatever its topic.

OpenAI is the only lab with a dedicated misalignment-report section (since
2026-09-16). Its RSS feed (`alignment.openai.com/rss.xml`) carries the blog
posts and none of the reports, which is why the page itself is watched.
Anthropic publishes misalignment findings across its alignment blog, its
research page and its risk reports, with no RSS feed. OpenAI also re-dates a
report when it updates one; the title filter ignores that, so updates don't
alert.

The two watches that came with the install (changedetection's changelog and
Hacker News) are the upstream examples.

## Checks

- UI: `curl -sk -o /dev/null -w '%{redirect_url}\n' https://changedetection.internal.tnwks.us/`
  goes to `oauth2.internal.tnwks.us/oauth2/start`.
- API: `curl -sk -o /dev/null -w '%{http_code}\n' https://changedetection.internal.tnwks.us/api/v1/watch`
  is 403 without the key, and 200 with `-H "x-api-key: <key>"`.
- Alert path: a temporary watch on `https://httpbin.org/uuid` with the same
  notification URL, then `GET /api/v1/watch/<uuid>?recheck=1`, then delete it.
  The watch's `notification_alert_count` goes up with `last_notification_error`
  false, and the message arrives in the War Room.
