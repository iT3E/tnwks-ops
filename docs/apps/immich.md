# Immich

Self-hosted photo library at `https://immich.internal.tnwks.us` (LAN and
admin VPN only, `nginx-internal`). Its first job is holding the photos the
kitchen screensaver shows: an hourly job mirrors the Google Photos shared
albums listed in Home Assistant into Immich, and Home Assistant's slideshow
reads them back through a read-only API key.

Manifests: `kubernetes/apps/selfhosted/immich/`. WSL-only (wired from
`kubernetes/clusters/wsl/apps/kustomization.yaml`), version
`ghcr.io/immich-app/immich-server` in `app/helmrelease.yaml`.

## Pieces

One app-template HelmRelease (`immich`, namespace `selfhosted`) with four
controllers:

| Controller         | What                                                                    | Storage                              |
| ------------------ | ----------------------------------------------------------------------- | ------------------------------------ |
| `server`           | API, web UI, background workers (thumbnails, metadata, nightly DB dump) | `immich-library` PVC at `/data`      |
| `machine-learning` | Face detection and CLIP smart search, on CPU                            | `immich-model-cache` PVC at `/cache` |
| `valkey`           | Job queue for the server                                                | none (emptyDir)                      |
| `postgres`         | `ghcr.io/immich-app/postgres` with VectorChord, dedicated to Immich     | `data-immich-postgres-0` PVC         |

The shared CNPG cluster in `databases` isn't used: it doesn't carry
VectorChord, and Immich's own image is what upstream tests against. The
`cache/valkey` instance is oauth2-proxy's session store, so Immich gets its
own queue.

## Where the data lives

- **Originals and thumbnails:** PVC `immich-library` on `local-path-bulk`,
  i.e. `/var/lib/rancher/k3s/storage/bulk/` inside whichever worker the
  server first ran on. That's the WSL distro disk, which lives on the Windows
  `E:` drive (`E:\WSL\Ubuntu\ext4.vhdx`). The PVC says 200Gi, but local-path
  doesn't enforce sizes: the real ceiling is E:'s free space, which
  `WindowsDiskCritical` watches. To grow it, raise `size` (the class allows
  expansion) and make room on E:.
- **Database:** PVC `data-immich-postgres-0` on `${STORAGE_CLASS_DATABASE}`.
  Immich dumps it nightly at 02:00 into the library volume
  (`/data/backups`, 14 kept).
- **Settings:** `app/config/immich.yaml`, loaded with `IMMICH_CONFIG_FILE`.
  While a config file is in use, the admin settings page is read-only, so
  change settings in git. The OAuth placeholders in it are filled from
  `immich-secret` by the `render-config` init container at pod start.
- **Users, albums, API keys:** in the database (UI-owned, not in git).

For now the screensaver albums in Immich are a mirror: Google Photos is the
source of truth, and losing the library means re-running the sync. Anything
uploaded to Immich by hand has no other copy.

## Login

Immich's built-in OAuth against tnwks-auth (Cognito app client `immich`,
`infrastructure/terraform/modules/aws/cognito.tf`), not oauth2-proxy: the
mobile app and the Home Assistant slideshow use Immich-issued sessions and
API keys that a proxy session would block.

- Immich can't map `cognito:groups` to roles (its role claim must hold the
  literal value `admin` or `user`), so **auto-register is off**. To let
  someone in, an admin creates their Immich user with their Cognito email;
  their first "Log in with tnwks" links to it. Admin rights are set on the
  Immich user.
- The login page shows the OAuth button and the password form
  (`autoLaunch: false`), so the password accounts below keep working.
- Logout goes through Cognito's `/logout` back to `/auth/login` (the
  client's registered `logout_uri`).

## Accounts

| Account           | Kind                       | Used by                           | Credentials                                            |
| ----------------- | -------------------------- | --------------------------------- | ------------------------------------------------------ |
| break-glass admin | Immich password, admin     | people, when OAuth is down        | workstation `~/.openclaw/credentials/immich-admin.env` |
| screensaver       | Immich password, not admin | owns every synced photo and album | workstation `~/.openclaw/credentials/immich.env`       |

The screensaver account owns the synced photos so nothing else in Immich can
be touched by the sync job, and so the read-only key Home Assistant uses
sees only those photos.

## Break-glass

- **OAuth broken (Cognito down, bad client secret):** log in with the
  break-glass admin's email and password on the normal login page.
- **Admin password lost:** reset it from the server pod. It prints a new
  password:
  ```bash
  kubectl -n selfhosted exec -it deploy/immich-server -- immich-admin reset-admin-password
  ```
  Store the new one in `~/.openclaw/credentials/immich-admin.env` (mode 600).
- **Turn OAuth off:** set `oauth.enabled: false` in `app/config/immich.yaml`
  and merge; the pod restarts on the ConfigMap change.
- **Restore the database:** the nightly dumps are in `/data/backups` on the
  library volume; Immich's admin page (Maintenance) restores one, or follow
  upstream's
  [backup and restore](https://docs.immich.app/administration/backup-and-restore)
  guide against the `immich-postgres-0` pod.

## Upgrades

Renovate groups the server and machine-learning images (`Immich`); minors are
reviewed (Immich migrates its database on start), patches automerge. Read the
release notes for breaking changes before merging a minor. The Postgres
image's major (17) only moves with a dump and restore.
