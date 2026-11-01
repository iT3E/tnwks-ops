# ImmichFrame

The kitchen display's screensaver: a slideshow of Immich's **Kitchen
screensaver** album with a clock and today's family events, at
`https://immichframe.internal.tnwks.us` (LAN and admin VPN only,
`nginx-internal`). The display shows it through the ImmichFrame Android app,
set as the tablet's Android screensaver (a "dream"), so it starts when the
display has been idle for 5 minutes and a tap goes back to the Home Assistant
dashboard.

Manifests: `kubernetes/apps/selfhosted/immichframe/`. WSL-only like Immich
(wired from `kubernetes/clusters/wsl/apps/kustomization.yaml`), image
`ghcr.io/immichframe/immichframe` in `app/helmrelease.yaml`. Upstream:
[immichframe.dev](https://immichframe.dev),
[server](https://github.com/immichFrame/ImmichFrame) and
[Android app](https://github.com/immichFrame/ImmichFrame_Android) (both
GPL-3.0).

## What it shows

- Photos from the **Kitchen screensaver** album only (the one the hourly
  sync job fills from Home Assistant's "Screensaver albums" list, see
  [`immich.md`](immich.md)). ImmichFrame re-reads the album every hour.
- 30 s per photo with a 2 s crossfade and a slow zoom. Photos fill the
  portrait screen: landscape photos are cropped at the sides rather than
  shown as a band with bars above and below.
- One photo at a time. ImmichFrame's `splitview` puts two portrait photos side
  by side, which on a portrait screen gives two narrow strips.
- Bottom left: the date ("Saturday, October 31") and a 12-hour clock.
- Top right: today's events from the family Google calendar, the same secret
  iCal address Home Assistant's `calendar.family` reads. ImmichFrame
  re-reads it every 15 minutes, the page asks every 10.
- No progress bar, photo date, location, album, people or tag captions, and no
  weather (see below).

Calendar quirks (upstream, v1.0.39.0):

- An all-day event shows as "Saturday, October 31 12:00 AM - Sunday,
  November 1 12:00 AM", because the event line reuses the clock's formats.
- A repeating event shows the right time of day, but it's sorted by the
  date of its first occurrence, so it can appear above an earlier event.

## Settings

Everything is in git:

- `app/config/Settings.yml`: ImmichFrame's settings file
  ([reference](https://immichframe.dev/docs/getting-started/configuration)).
- `app/config/custom.css`: served as `/static/custom.css`, tuned for the
  display's 720x1280 CSS viewport. It shows the events on a screen narrower
  than 1024 px (upstream hides them there), sizes the clock and events for the
  wall, and makes the photo box fill the screen (upstream's "fill" leaves a
  landscape photo as a band on a portrait screen).

ImmichFrame keeps its settings in a SQLite database and imports a settings
file only on first start; after that, upstream expects changes in its admin UI
at `/admin`. Here the database is an `emptyDir`, so every pod start is a first
start and imports `Settings.yml` again. The admin UI stays shut: with no admin
password set it shows "disabled". Don't set `IMMICHFRAME_ADMIN_PASSWORD`;
changes made there would be lost at the next restart anyway.

To change a setting, edit `Settings.yml` or `custom.css` and merge. The
ConfigMap change restarts the pod (reloader), and the display loads the new
settings the next time the screensaver starts. To try CSS first, run the image
locally with the file mounted at `/app/wwwroot/static/custom.css` and render
it at 720x1280.

Upstream has deprecated the settings-file import ("will be removed in a
future version", logged at start). If a release drops it, the pod starts with
no account and the slideshow shows a "no accounts" hint; that's why every
ImmichFrame update is a reviewed Renovate PR (`impact/high`, see
`.github/renovate/autoMerge.json5`). The fix then is to seed the database
from git another way (e.g. its admin API), not to start editing settings by
hand.

### Weather

Off: there is no OpenWeatherMap key. To turn it on, put the key in
`immichframe-secret` (`WEATHER_API_KEY`), add `WeatherApiKey:
"__WEATHER_API_KEY__"` to `Settings.yml` with its `sed` line and env var in
the `render-config` init container, and set `WeatherLatLong` (from
`zone.home`; keep the coordinates in the Secret too, the repo is public) and
`ShowWeatherDescription`. The weather shows under the clock.

## Secrets

`app/secrets.sops.yaml` (`immichframe-secret`):

| Key               | What                                                                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `IMMICH_API_KEY`  | The screensaver account's ImmichFrame key, mounted as a file (`ApiKeyFile`). Workstation copy: `~/.openclaw/credentials/immich.env` (`IMMICH_FRAME_API_KEY`).                   |
| `AUTH_SECRET`     | ImmichFrame's `AuthenticationSecret`. Workstation copy: `~/.openclaw/credentials/immichframe.env`.                                                                              |
| `WEBCALENDAR_URL` | The family calendar's secret iCal address, copied from Home Assistant's Remote Calendar entry (`calendar.family`). Workstation copy: `~/.openclaw/credentials/family-ical.env`. |

The `render-config` init container fills the `Settings.yml` placeholders
from the Secret with `sed`, so a value can't contain `|`, `&`, `\` or `"`.

**Why no Cognito:** the Android app's WebView can't do the OIDC login, so
there's no oauth2-proxy in front. The `AuthenticationSecret` protects the
slideshow, calendar and weather API instead: every request needs it as a
Bearer token, and the web page takes it once as `?authsecret=...` and keeps
it in local storage. Without it the page loads but shows no photos. Only the
display settings (`/api/Config`) are anonymous.

**Changing the secret** (or the calendar address): update the Secret with
`bin/sops`, merge, then set the new secret in the app on the display (below).
If the family calendar's secret address is reset in Google Calendar, update
both Home Assistant's Remote Calendar entry and `WEBCALENDAR_URL`.

ImmichFrame logs a calendar's full address when it fails to download it, so
treat this pod's logs as holding the secret on those lines.

## The kitchen display

Apolosign, Android 16, 2160x3840 portrait; driving it over ADB is in Ori's
`android-device-adb` skill.

- **App:** `com.immichframe.immichframe` v50 (versionCode 50), installed from
  Google Play, which keeps it updated (it isn't in Renovate). `adb install`
  of the
  [release APK](https://github.com/immichFrame/ImmichFrame_Android/releases)
  fails on this display with `INSTALL_FAILED_USER_RESTRICTED`, though no
  Android user restriction is set (it looks like the vendor firmware only
  takes installs through Play). The Play build is the same
  source revision as the release (identical `classes.dex`), signed by Play.
- **App settings** (open the ImmichFrame app, it opens its settings when no
  server is set; otherwise swipe down or press up): Server URL
  `https://immichframe.internal.tnwks.us`, Authorization Secret = the
  `AUTH_SECRET`, "Use WebView?" on. The app adds `?authsecret=` itself.
- **Screensaver:**
  ```bash
  adb shell settings put secure screensaver_components com.immichframe.immichframe/.ScreenSaverService
  adb shell settings put secure screensaver_enabled 1
  adb shell settings get system screen_off_timeout   # 300000: dream after 5 idle minutes
  adb shell am start -n com.android.systemui/.Somnambulator   # start the screensaver now (test)
  ```
  A tap ends it and shows whatever was underneath (the Home Assistant
  dashboard) without passing the tap on.
- The app's own full-screen activity also starts a small unauthenticated
  remote-control server on port 53287 (next, previous, brightness), and it
  keeps running until that activity is destroyed. The screensaver doesn't
  start it, so after opening the app (to change its settings) run
  `adb shell am force-stop com.immichframe.immichframe` and go back to the
  dashboard.

**Restore the previous screensaver** (Google Photos, which the Apolosign's
clock-and-agenda overlay draws over):

```bash
adb shell settings put secure screensaver_components com.google.android.apps.photos/com.google.android.apps.photos.daydream.PhotosDreamService
```

The ImmichFrame server can stay; scale it to zero or remove the ks from the
WSL apps kustomization if it's no longer wanted.

## Troubleshooting

- **Display shows ImmichFrame's error page or "unauthorized":** the app's
  secret doesn't match `AUTH_SECRET`, or the host doesn't resolve from the
  LAN (`*.internal.tnwks.us` is the router's wildcard to the Windows host's
  ingress port proxy, see [`../wsl/lan-exposure.md`](../wsl/lan-exposure.md)).
- **No photos, pod log says the Immich server is unreachable:** check
  Immich, then the API key (Immich user screensaver, key
  `immichframe kitchen screensaver (read-only)`).
- **Look at it like the display does:** open
  `https://immichframe.internal.tnwks.us/?authsecret=<secret>` in a browser
  at 720x1280.
