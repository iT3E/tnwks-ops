# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

## v1.158.0 - 2026-11-06

### Changed

* Mealie is stopped on homelab-wsl while its database moves to its own CNPG cluster (step 1 of 2). Its replica count is a per-app site switch now (`MEALIE_REPLICAS`, default 1).

## v1.157.0 - 2026-11-06

### Added

* playwright-mcp also runs on homelab-wsl-02, the pilot of moving an app there. Its replica count is the first per-app site switch (`PLAYWRIGHT_MCP_REPLICAS`, default 1; set per cluster in cluster-settings, at runtime in site-overrides).

## v1.156.0 - 2026-11-06

### Changed

* Home Assistant person alerts are set per adult: each adult has their own box on the Security dashboard's Alerts view (night window with ring-through-DND, away mode, day/time schedule) with a plain-words "Right now" line, decided by one shared template; the push text names the rule that sent it.

## v1.155.0 - 2026-11-06

### Changed

* homelab-wsl-02: the Alertmanager Watchdog alone keeps healthchecks.io `homelab-wsl-02` alive (the interim CronJob is gone), node-exporter runs there, and kopiur's PrometheusRule and ServiceMonitor are back on (after kube-prometheus-stack).

## v1.154.0 - 2026-11-06

### Added

* homelab-wsl-02 monitors itself: kube-prometheus-stack (the chart's Kubernetes rules plus the heartbeat, OOM, resource-pressure and site-override alerts) and its own Alertmanager to the same War Room. Alerts carry `cluster=<CLUSTER_NAME>` (was a fixed `homelab-wsl`). Its Watchdog pings healthchecks.io `homelab-wsl-02`, its delivery heartbeat the new `alert-delivery-wsl-02`. Windows host and app alerts stay on homelab-wsl, which scrapes both hosts.

## v1.153.0 - 2026-11-05

### Added

* homelab-wsl-02 runs kopiur: its own kopia repository at `kopiur-homelab-wsl-02/` (the shared ClusterRepository takes `KOPIUR_PREFIX`, default `kopiur/`, so homelab-wsl is unchanged) and homelab-wsl's repository read-only (`homelab-wsl`: no maintenance, adoption Ignore) for restores of its snapshots. Snapshot policies come with the apps.

## v1.152.0 - 2026-11-05

### Added

* homelab-wsl-02 has an off-site dead-man switch: a CronJob pings the new healthchecks.io check `homelab-wsl-02` every 2 minutes, and healthchecks.io messages Telegram when it goes quiet.

## v1.151.0 - 2026-11-05

### Added

* homelab-wsl-02 gets its backup credentials: a Roles Anywhere certificate (CN `homelab-wsl-02-backups`, issued 2026-11-05, valid a year) and the shared refresher CronJob, which turns it into 4-hour STS sessions (`aws-backup-sts` in `databases` and `kopiur-system`) for role `tnwks-rolesanywhere-backups-wsl-02`: reads the backup bucket, writes only homelab-wsl-02's prefixes.

## v1.150.1 - 2026-11-05

### Fixed

* cert-manager checks DNS-01 propagation over DNS-over-HTTPS: plain DNS is redirected to the MikroTik, whose `*.internal.tnwks.us` record answers the challenge name with NXDOMAIN, so the internal wildcard certificate could not be issued (homelab-wsl-02) or renewed (homelab-wsl, due 2026-11-22).

## v1.150.0 - 2026-11-05

### Added

* WSL-02 exposes homelab-wsl-02's internal ingress on the LAN (`10.10.91.112:80/443`), the same portproxy -> socat chain as DESKTOP-HKE05KI. `tnwks-lan-bridge` takes its target addresses per host (`wsl_lan_bridge_targets`, a systemd drop-in); without them the units are unchanged.

## v1.149.0 - 2026-11-05

### Added

* homelab-wsl-02 runs cert-manager (Let's Encrypt DNS-01 issuers, the internal and public wildcard certificates), ingress-nginx-internal on its LoadBalancer IP 10.6.0.200, and the CNPG operator with the barman-cloud plugin. Its Flux key joins the shared `kubernetes/` SOPS rule (every movable app runs on both clusters); the two secrets it needs now are re-keyed.

## v1.148.1 - 2026-11-05

### Fixed

* Docs: a Windows feature update removes the OpenSSH Server capability (seen on WSL-02, 23H2 to 25H2), so the sshd bootstrap has to run again after one; `windows-patching.md` and `windows-ansible.md` say so, and record WSL-02 on 25H2.

## v1.148.0 - 2026-11-05

### Changed

* The public tunnel DNS records live in `modules/cloudflare/tunnel-dns.tf` (inputs `tunnel_id`, `tunnel_public_hosts`, `tunnel_host_names`); the Cloudflare root only passes them, with `moved` blocks for the 16 records. No DNS changes.

## v1.147.0 - 2026-11-05

### Added

* homelab-wsl's Prometheus scrapes the other site's Windows host (WSL-02) over the LAN, next to its own (`PEER_WINDOWS_HOST_ADDR`/`_NAME`, `WINDOWS_HOST_NAME` in cluster-settings), and the Windows alerts (CPU, memory, commit, paging, disk, host down, Ansible drift) apply to every scraped host per `instance` instead of only DESKTOP-HKE05KI.

## v1.146.0 - 2026-11-05

### Removed

* external-dns is gone: the public Cloudflare records are in Terraform (`environments/prod/cloudflare/public-dns.tf`, #1736), so the controller, its HelmRepository, the `external-dns.alpha.kubernetes.io/target` and `hostname` annotations, the tunnel's `DNSEndpoint` and `cloudflared`'s dependency on it are removed. A new public app is a line in `local.cf1_public_hosts`.

## v1.145.0 - 2026-11-05

### Changed

* The public Cloudflare records (the app names behind the tunnel and `ingress` itself) are managed by Terraform (`environments/prod/cloudflare/public-dns.tf`), adopted with `import` blocks from what external-dns created. Adding a public app is a line in `local.cf1_public_hosts`. external-dns stays in `upsert-only` until it is removed in the next step.

## v1.144.0 - 2026-11-05

### Changed

* external-dns runs with `policy: upsert-only` instead of `sync`: it no longer deletes Cloudflare records. First step of moving the public records to Terraform; until then a second external-dns (homelab-wsl-02) can't delete homelab-wsl's records.

## v1.143.0 - 2026-11-05

### Changed

* Per-app CNPG clusters (`kubernetes/components/cnpg-cluster`) store their data on the cluster's default class (`local-path`, ext4 on every worker) instead of `STORAGE_CLASS_DATABASE` (worker-1-only `local-path-ext4` on homelab-wsl, which has no room), so each lands on whichever worker has room; `CNPG_STORAGE_CLASS` overrides it. The README explains two instances for an app whose database should move between workers.

### Removed

* Drop the shared Postgres cluster's `app` database: Mealie's database until 2026-11, kept since as its rollback copy, unused (no connections, same data as `mealie` from 2026-11-01). A CNPG `Database` with `ensure: absent` drops it on the running cluster.

## v1.142.1 - 2026-11-05

### Fixed

* The `tnwks-wsl-keepalive` task template carries the `IdleSettings` block Windows adds to every task, so the nightly check no longer reports WSL-02 as drifted (`changed=1` on that task every run).

## v1.142.0 - 2026-11-05

### Added

* WSL-02's windows_exporter and smartctl_exporter also admit DESKTOP-HKE05KI's address, so homelab-wsl's Prometheus can scrape the other site's Windows host over the LAN (`windows_monitoring_remote_extra`, per host).

## v1.141.0 - 2026-11-05

### Added

* Runtime switchover hook (`docs/wsl/multi-site.md`, "Switching an app"): every app Kustomization on both WSL clusters reads an optional ConfigMap `flux-system/site-overrides` after `cluster-settings` and `cluster-secrets`, so a value there wins. Nothing in git creates it; `SiteOverrideLingering` fires when one exists for more than a day. No effect until one is created.

## v1.140.0 - 2026-11-05

### Added

* AWS: a Roles Anywhere backup role per extra site (`rolesanywhere_backups_sites`), first `wsl-02`: certificate CN `homelab-wsl-02-backups`, profile `tnwks-ops-backups-wsl-02`. It reads the whole backup bucket (to replay and restore homelab-wsl's backups) and writes only its own prefixes (`cnpg/*/homelab-wsl-02*/*`, `kopiur-homelab-wsl-02/*`). homelab-wsl's role is unchanged.

## v1.139.1 - 2026-11-05

### Fixed

* Postgres backup alerts work per CNPG cluster: `PostgresBackupStale` groups by cluster instead of namespace (a fresh backup of one cluster hid a stale one in the same namespace), and `PostgresWALArchivingStalled` fires when archiving keeps failing, not when an idle database has written no WAL for an hour. Ready for one cluster per app (`kubernetes/components/cnpg-cluster`).

## v1.139.0 - 2026-11-05

### Added

* `05-talos-upgrade.yml` rolls any Talos-in-Docker cluster in `talos_clusters` (`-e talos_target=wsl-02`, `task talos:upgrade:wsl-02`); node names and health-check IPs come from the cluster entry. `01-talos-bootstrap.yml` writes the talosconfig to `~/.talos/config-<cluster>`, where the upgrade playbook and `mint-talosconfig.sh` look for it.

## v1.138.0 - 2026-11-05

### Added

* Reusable Kustomize component `kubernetes/components/cnpg-cluster`: an app's own CloudNativePG cluster in `databases` with small-cluster defaults (`shared_buffers` 32MB, `max_connections` 20), barman-cloud WAL archiving and daily base backups to `cnpg/<app>/<site>/` in the backup bucket. Set per app through `postBuild.substitute`; no app uses it yet.

## v1.137.0 - 2026-11-05

### Added

* Cluster definition for `homelab-wsl-02` (Talos in Docker on WSL-02): `talos/wsl-02` (multi-document patches for talosctl >= 1.14), `kubernetes/clusters/wsl-02` with Cilium as CNI and LoadBalancer (LB IPAM + L2 announcements, no MetalLB), local-path on the distro's ext4 vhdx, its own SOPS age recipient, and Flux applied from committed manifests over HTTPS (no `flux bootstrap`, nothing pushed to main). The bootstrap playbooks take any Docker-provisioned cluster from `talos_clusters`, and `02-cni-bootstrap.yml` installs Cilium with the HelmRelease's own values. The shared Cilium HelmRelease drops `CAP_SYS_MODULE` (Talos' Cilium values), which is what kept Cilium off WSL; the docs that said it can't run there are corrected. `task bootstrap:wsl-02`.

## v1.136.0 - 2026-11-05

### Added

* Distro role `wsl_cluster_tools` installs the CLIs the Talos bootstrap runs (talosctl, kubectl, helm, flux, sops, age, uv), pinned by version and SHA-256, on a distro without Homebrew. On for WSL-02, where homelab-wsl-02 will be bootstrapped.

## v1.135.0 - 2026-11-05

### Changed

* Renovate review: Ori only reviews an impact/high PR after the owner adds the new owner/review label; a Renovate push clears it again.

## v1.134.1 - 2026-11-05

### Fixed

* WSL-02's distro stays up: WSL stops a distro seconds after its last `wsl.exe` client exits, and nothing on WSL-02 held one open, so the watchdog's restart only lasted until its next run. New per-host task `tnwks-wsl-keepalive` (one `wsl.exe` client from boot) and watchdog switch `-KeepAliveTask`, which starts it again after a WSL restart. On for WSL-02 only.

## v1.134.0 - 2026-11-05

### Changed

* ARC runners: Ansible Hosts runs its sites side by side (tnwks-ansible scale set to 2 runners) and caches the Galaxy collections; per-task timings in the job summary; controller and listener metrics scraped, with a Grafana dashboard; sites-builder drops the unused dind sidecar, pins its runner image and scales from zero.

## v1.133.1 - 2026-11-05

### Fixed

* The Ansible downloads of pinned artifacts (Coral detector, monitoring agents, usbipd-win, Docker's apt key) wait up to 120s and retry 3 times instead of failing at the 10s default, which made the Ansible Hosts plan job fail twice in a row on slow GitHub downloads.

## v1.133.0 - 2026-11-05

### Added

* The WSL watchdog and the vhdx compaction task run on WSL-02: `Watch-TnwksWsl.ps1` takes `-SkipGateway` / `-SkipUsb` (per host, `wsl_watchdog_args`) so it only keeps WSL and the distro up where there is no OpenClaw gateway or Z-Stick, and `Compact-WslVhdx.ps1` gets the distro's vhdx per host (`vhdx_compact_vhdx`) and logs the free space of that drive. DESKTOP-HKE05KI's tasks are unchanged.

## v1.132.1 - 2026-11-05

### Fixed

* `windows_monitoring` creates the windows_exporter and smartctl_exporter directories before writing into them, so the first apply on a fresh Windows host (WSL-02) no longer fails at `config.yaml`.

## v1.132.0 - 2026-11-05

### Added

* Ansible site `site_wsl02` for WSL-02, the second WSL host, checked and applied by CI from homelab-wsl's runner, which reaches it over the LAN (2222, from DESKTOP-HKE05KI only) and jumps through the distro to Windows sshd (`wsl_ci_access` `permit_open`). New distro role `wsl_docker` installs Docker Engine from Docker's apt repository (pinned to 28.3.1, a no-op on DESKTOP-HKE05KI). Roles for hardware a host doesn't have (Coral, Z-Stick, UDP relays, backups, the watchdog) are skipped when their per-host list is empty, and `windows_host_settings` skips the Ethernet checks on a Wi-Fi host. WSL-02's unused hand-made llama.cpp portproxy and firewall rule are removed.

## v1.131.0 - 2026-11-05

### Added

* DHCP reservation for WSL-02 (10.10.91.112), the Windows host for the second WSL cluster `homelab-wsl-02`. It is on the wifi NIC for now; swap the MAC when the box is wired.

## v1.130.0 - 2026-11-05

### Changed

* CI: the secret scan, privacy, label, changelog and release workflows now call the reusable workflows in iT3E/github-workflows (v1.4.1) instead of keeping their own copies, and PRs that touch workflows get an actionlint check. The required checks are now named betterleaks / betterleaks and privacy / privacy.

## v1.129.1 - 2026-11-05

### Fixed

* Ansible Hosts: the summary and the drift metrics counted a host twice when one run covered both host playbooks (each playbook prints a cumulative recap); `drift` can be dispatched by hand.

## v1.129.0 - 2026-11-05

### Added

* Ansible host config is GitOps: `.github/workflows/ansible-hosts.yaml` runs `windows-host.yml` and `wsl-distro.yml` per site from a new self-hosted `tnwks-ansible-<cluster>` runner, posting a `--check --diff` summary on PRs, applying on merge, and replacing the WSL systemd timer as the nightly drift check (now covering the WSL distro too). Logs and PR comments carry only recap counts and task names; the diffs are an age-encrypted artifact. The runner has its own SSH and age keys (docs/operations/ansible.md).

## v1.128.0 - 2026-11-05

### Changed

* mikrotik: allow ICMP from the desktop to the 3 UniFi APs and SW03, so smokeping's AP and switch pings (and `WifiAPUnreachable`) no longer depend on the desktop's wg01-home VPN tunnel

## v1.127.0 - 2026-11-05

### Changed

* mikrotik: allow SNMP (udp/161) from the desktop to SW03 only, so the snmp-exporter scrape of the AP ports no longer depends on the desktop's wg01-home VPN tunnel (new zone pair `190-unifi-frontend-910-switch-mgmt`, also in the generator's `MONITORING_ZONE_RULES`)

## v1.126.0 - 2026-11-05

### Added

* Home Wi-Fi monitoring: a git-managed "Home Wi-Fi" Grafana dashboard, Wi-Fi alerts in `wifi.yaml` (unpoller blind, AP uplink below 1G, AP unreachable, AP switch port flapping, channel busy, retries, satisfaction, AP reboot outside the upgrade window), smokeping pings of the three APs and SW03, and an snmp-exporter scrape of the AP ports on SW03.

## v1.125.0 - 2026-11-05

### Added

* Ansible now owns the last hand-managed Windows host pieces: usbipd-win is pinned (MSI checked by SHA-256) and the Z-Stick's forced bind is enforced (role `windows_usb_attach`), and the tcp/2222 (WSL sshd) and tcp/18789 (OpenClaw gateway) forwards are LAN bridge ports, so their portproxy entries follow the WSL address; `tnwks-lan-bridge-2222`/`-18789` replace the hand-made rules, with the same any-address scope.

## v1.124.0 - 2026-11-05

### Added

* CI for Ansible (`.github/workflows/ansible.yaml`): every PR touching `infrastructure/ansible/` syntax-checks each playbook and runs ansible-lint (production profile, `.ansible-lint`) on the pinned controller; `task ansible:lint` runs the same locally.

## v1.123.0 - 2026-11-05

### Changed

* unifi: 5 GHz Roaming Assistant at -73 dBm, Kitchen 5 GHz power to medium and Master 2.4 GHz power to high, from the 2026-11-04 Wi-Fi analysis; the module now takes optional per-radio `tx_power_mode`/`tx_power` and `wlan.roaming_assistant_5g_rssi`

## v1.122.0 - 2026-11-05

### Added

* Ansible role `windows_sshd`: the nightly check now covers the Windows OpenSSH server Ansible connects through, by running `Install-TnwksWindowsSshd.ps1 -Check` (check only by default, since rewriting sshd_config restarts sshd).

### Changed

* Ansible: every Windows host and WSL distro script now ships with the role that deploys it (`roles/<role>/files/`), and `infrastructure/wsl-host/` is gone. The UniFi and Aruba backup timers (role `wsl_backups`) and the nightly Windows drift check (role `wsl_drift_check`) are Ansible roles on the WSL distro instead of `sudo install` tasks.

## v1.121.0 - 2026-11-05

### Changed

* Ansible: one inventory (`inventory/hosts.yml`) with `windows_hosts`, `wsl_distros`, `mikrotik` and per-site groups; one pinned ansible-core for every playbook (uv: `pyproject.toml` and `uv.lock`); `ansible.cfg` paths that resolve; pinned SSH host keys; the Windows host account in SOPS; `playbooks/wsl-distro.yml` replaces the per-role WSL playbooks (docs/operations/ansible.md).

### Removed

* Ansible: the k3s, kube-vip, Kasm and Proxmox playbooks, roles and inventory, and the unused Galaxy roles and collections.

## v1.120.0 - 2026-11-05

### Changed

* terraform: rename the Cloudflare TFC workspace to `tnwks-cloudflare-prod` (same workspace and state, now in the AWS project and declared in `modules/tfc`, adopted by `aws-init` imports), and drop the healthchecks `moved` block now that the runner has applied it

## v1.119.0 - 2026-11-05

### Changed

* terraform: move the healthchecks.io checks into `modules/healthchecks` with `environments/prod/healthchecks` as a thin root (`moved` blocks, no checks recreated), and plan/apply both the healthchecks.io and Cloudflare roots in CI on the self-hosted runner (`healthchecks-{plan,apply}.yaml`, `cloudflare-{plan,apply}.yaml`, shared `.github/actions/runner-terraform`) instead of from the desktop

## v1.118.1 - 2026-11-04

### Fixed

* Frigate sv3c_west: a motion mask over the eave and the clock overlay (which counted as motion all night and kept the playhouse in front of the detector) and a person mask on the playhouse's front wall, which the Coral model scored as a person at night and set off two false night alerts.

## v1.118.0 - 2026-11-04

### Added

* Check-only Ansible role windows_host_settings (.wslconfig byte for byte, Ethernet power settings, Wi-Fi off, High performance plan; drift fails the run, nothing is ever set on the NIC) and a nightly drift check: the WSL timer tnwks-windows-drift-check runs the Windows --check against main and reports through windows_exporter's new textfile collector, with alerts WindowsHostConfigDrift and WindowsHostDriftCheckStale.

## v1.117.0 - 2026-11-04

### Changed

* The Windows host's USB attach task (Z-Stick only now, Coral handling off by default), the WSL watchdog and the vhdx compaction task are Ansible-managed (roles windows_usb_attach, windows_wsl_maintenance) with their scripts in the repo; the watchdog stops trying to attach the Coral, and the WSL role wsl_usb_attach keeps the Coral's unit disabled.

## v1.116.0 - 2026-11-04

### Changed

* The Windows UDP relays (role windows_udp_relay) and the Windows half of the TCP LAN bridge (windows_lan_bridge: sync script, ports.json, firewall rules, the tnwks-lan-bridge-sync task) are now managed by Ansible; the Register-*.ps1 scripts are removed. Sync-TnwksLanBridge.ps1 reads its ports from ports.json, leaves entries that already point at WSL alone, and no longer touches firewall rules.

## v1.115.0 - 2026-11-04

### Changed

* The Windows host's monitoring agents (windows_exporter, smartmontools, smartctl_exporter, their task and firewall rules) are now the Ansible role windows_monitoring, run by task wsl-host:monitoring and wsl-host:monitoring-check; Install-TnwksWindowsMonitoring.ps1 is removed. Scheduled tasks on the host are declared as whole task XML through the new tnwks_win_scheduled_task module.

## v1.114.0 - 2026-11-04

### Changed

* Frigate detects objects on the USB Coral again, now served natively by the Windows host's tnwks-coral-detector-0 service through Frigate's zmq detector instead of usbip, which takes OpenVINO off the WSL CPU.

## v1.113.1 - 2026-11-04

### Fixed

* FinanceSyncNeverSucceeded waits until a finance CronJob's first run was actually due, so a new weekly job no longer alerts days before its first Monday run.

## v1.113.0 - 2026-11-04

### Added

* Prometheus scrapes the Windows Coral detector services (job coral-detector), with alerts CoralDetectorDown, CoralDetectorNotReady and CoralDetectorNoInferences; the last only applies to a Coral that a Frigate detector named coral<id> uses.

## v1.112.0 - 2026-11-04

### Added

* The Windows host is managed with Ansible over a WSL-only, key-only OpenSSH server (task wsl-host:sshd, wsl-host:windows-check/-apply), and its first role runs each USB Coral as a Windows service (tnwks-coral-detector-<id>) that serves Frigate's zmq detector with Prometheus metrics.

## v1.111.4 - 2026-11-04

### Fixed

* Frigate's OpenVINO detector actually runs: the model is set explicitly so the camera processes no longer crash at start.

## v1.111.3 - 2026-11-04

### Fixed

* Frigate detects objects with OpenVINO on the CPU instead of the USB Coral, so the WSL vhci_hcd seqnum overflow can no longer hang detection every few days.

## v1.111.2 - 2026-11-04

### Fixed

* Kitchen display holiday effects (falling emoji, crossing characters) stay on the Kitchen dashboard instead of following you to other Home Assistant pages; cards 1.12.1.

## v1.111.1 - 2026-11-04

### Fixed

* Alertmanager's scraped series now carry container="alertmanager" (static relabel in its ServiceMonitor), which the stock Alertmanager* rules also select. The operator derives that label only from a declared container port, and the app-template pod declares none, so after #1673 the rules still matched nothing.

## v1.111.0 - 2026-11-04

### Added

* Off-site check alert-delivery on healthchecks.io: AlertDeliveryHeartbeat fires while Alertmanager can deliver to Telegram and pings it every 2 minutes, so a revoked or hijacked War Room bot, or the Telegram API being down, reaches a DM through healthchecks.io's own bot.

### Fixed

* Prometheus now scrapes Alertmanager (ServiceMonitor, job relabeled to the one the stock rules select, container renamed to alertmanager). The nine stock Alertmanager* rules, AlertmanagerFailedToSendAlerts included, matched no series before and could never fire.

## v1.110.0 - 2026-11-03

### Added

* Off-site heartbeats on healthchecks.io: Alertmanager pings the homelab-wsl check with its Watchdog alert, the RB5009 pings sce-rtr01, and healthchecks.io messages Telegram when either goes quiet, so a power cut, WAN outage or a cluster that does not come back gets reported (docs/operations/external-monitoring.md).

## v1.109.1 - 2026-11-03

### Fixed

* Frigate: the front door camera hears speech now. Its own audio noise reduction was sending constant silence (switched off on the camera), and speech detection there now runs from RMS 50 instead of 500 (ambient about 27).

## v1.109.0 - 2026-11-03

### Removed

* alertmanager: FinanceReconciliationDrift goes to the null receiver instead of Telegram. It mostly fired on timing (a balance moving before its transactions arrive) after the sync had already corrected it, so it gave nothing to act on.

## v1.108.1 - 2026-11-03

### Fixed

* Frigate: speech detection on the south camera runs from RMS 300 instead of the default 500. Ambient there is about 230, so most speech never reached the detector or the transcriber.

## v1.108.0 - 2026-11-03

### Added

* Home Assistant: schedule card on the Security Alerts view (on/off, day-of-week buttons, time pickers); the weekday person alerts take their days from it

## v1.107.2 - 2026-11-03

### Fixed

* Home Assistant: night person alerts go only to the two adult phones, no longer to every Companion app device (the kitchen wall tablet rang on the alarm stream)

## v1.107.1 - 2026-11-03

### Fixed

* Home Assistant: Security alerts view, the away/weekday automation tile name no longer gets cut off

## v1.107.0 - 2026-11-03

### Added

* transcriber: daily summaries. The UI's Summarize button sends the day's transcript to claude-sonnet on Bedrock through LiteLLM, with the transcriber's own key (claude-sonnet only); the ingress waits up to 180 s for it.

## v1.106.1 - 2026-11-03

### Fixed

* Home Assistant: away and weekday person alerts no longer stay silent in the night window while the night alerts are switched off

## v1.106.0 - 2026-11-03

### Added

* LiteLLM: claude-sonnet on Bedrock, with credentials from the existing Roles Anywhere certificate through an aws_signing_helper sidecar (no new AWS resources), virtual keys kept in step with a SOPS list by an ensure-keys sidecar (the transcriber's, limited to claude-sonnet), and an alert 30 days before the certificate expires.

## v1.105.0 - 2026-11-03

### Added

* Home Assistant: away and weekday person alerts (normal push to named phones), and the Security dashboard alert settings now survive restarts and deploys

## v1.104.0 - 2026-11-03

### Changed

* transcriber: the web UI. The transcriber 0.2.0 API runs as a second container in its pod, the tnwks-transcriber-web 0.1.0 frontend as its own Deployment, on transcriber.internal behind oauth2-proxy for the Cognito group transcriber only.

## v1.103.0 - 2026-11-03

### Added

* Cognito: a transcriber group with only the admin in it, for the transcripts' ingress (oauth2-proxy allowed_groups).

## v1.102.1 - 2026-11-03

### Fixed

* CI: "Image exists" now also checks images added in plain manifests (Deployments such as packing-api and the storefront API), not only those flate reports from HelmReleases. Before, those PRs skipped the check, so an automerged image bump there was gated by the render alone.

## v1.102.0 - 2026-11-03

### Changed

* Renovate: our own images (ghcr.io/it3e/*) automerge behind Build Success at every update type, any time of day, and never get an impact label. packing-api and the storefront API are pinned to their releases (<version>@sha256) instead of the floating :main tag, so Renovate can follow them.

## v1.101.0 - 2026-11-03

### Added

* transcriber: speech on the front door and south cameras, transcribed (Whisper, local on CPU) and diarized, with consistent per-person labels from enrolled voiceprints, into a new cnpg transcriber database (pgvector). Clip audio kept 90 days on local-path-bulk. Its own MQTT login and database role.

## v1.100.1 - 2026-11-03

### Fixed

* Frigate: no audio detection on the west camera. Its stream advertises PCMA but sends no audio packets, so the audio process only idled.

## v1.100.0 - 2026-11-03

### Added

* Frigate audio detection (speech) on the front door, south and west cameras, whose streams carry audio. Its speech ON/OFF topic is the trigger for the coming transcription service. Kitchen, balcony and pool have no microphone.

## v1.99.0 - 2026-11-03

### Changed

* finance-sync 0.2.0: state moved from the PVC into its own Postgres database (transactional writes, point-in-time restore); the PVC is kept unmounted as the rollback copy.

## v1.98.0 - 2026-11-03

### Changed

* kitchen display: each holiday theme is now an illustrated scene behind the page (a sky behind the clock and a lit, layered landscape along the bottom: a harvest moon over autumn hills and a barn, an aurora over snowy pines and a cabin, a haunted house under a full moon, heart trees and balloons, a rainbow over a cottage and sheep, a skyline under fireworks or bokeh), with the cards floating over it lit and shadowed instead of patterned, a lit tab pill, and a countdown to the holiday in the greeting.

## v1.97.1 - 2026-11-03

### Fixed

* kitchen display: any theme can be picked any time of year. A holiday theme only switches itself back to Everyday in the week after its holiday when it was left on through it; one picked after the holiday stays, on the wall display, phones and the screensaver.

## v1.97.0 - 2026-11-03

### Added

* kitchen display: holiday themes dress the cards themselves (patterns, snow, drips, lace, cobwebs, holly and other ornaments, frames, candy-striped bars), with string lights or bunting for garlands, taps and confetti in the holiday's emoji and colors, and characters that cross the screen now and then. Theme and mode are now per HA user: the wall display keeps its own (and its screensaver's), and nobody else's screen follows it unless they pick.

## v1.96.0 - 2026-11-03

### Added

* finance-sync 0.1.3: FinanceBankConnectionBroken (one per broken bank connection, reminded daily) and FinanceRefreshTimeout alerts, pushed by the sync to Alertmanager in place of the weekly log grep.

## v1.95.0 - 2026-11-03

### Added

* cloudnative-pg: finance_sync role and database for the finance-sync jobs' state, with its login in finance-sync-secret.

## v1.94.1 - 2026-11-03

### Fixed

* firefly: the daily cron runs at 03:00 Pacific (Firefly's own time zone) instead of 03:00 UTC, the evening before.

## v1.94.0 - 2026-11-03

### Changed

* finance-sync: enabled (cutover from the host crontab), image 0.1.2 which pushes reconciliation drift to Alertmanager.

## v1.93.0 - 2026-11-03

### Added

* finance-sync: the daily Simplifi sync and weekly Amazon enrichment as CronJobs (suspended until the cutover from the host crontab), with a backed-up state PVC, staleness alerts and docs/apps/finance-sync.md.

## v1.92.0 - 2026-11-03

### Changed

* mealie: use its own `mealie` database instead of `app`, the cluster's first-boot database (kept as the rollback copy).

## v1.91.0 - 2026-11-03

### Added

* cloudnative-pg: an empty `mealie` database owned by the mealie role, for Mealie to move into from `app`.

## v1.90.0 - 2026-11-03

### Changed

* cloudnative-pg: stop managing the app database with a Database CR (kept as Mealie's rollback copy), freeing the mealie name for its own database.

## v1.89.1 - 2026-11-03

### Security

* cloudnative-pg: the shared app role owns nothing and can no longer log in; postgres-app is no longer reflected into app namespaces and the cluster-wide POSTGRES_* variables are gone. The one-time ownership Jobs are removed.

## v1.89.0 - 2026-11-03

### Changed

* home-assistant, litellm, the storefront API, packing-api, vaultwarden and mealie connect to Postgres as their own roles instead of the shared app role; vaultwarden no longer uses the cluster-wide POSTGRES_* variables.

## v1.88.0 - 2026-11-03

### Changed

* cloudnative-pg: own managed roles for Home Assistant, LiteLLM, the storefront API, the packing API, Vaultwarden and Mealie; each owns its database and a one-time Job moves its objects off the shared app role.

## v1.87.1 - 2026-11-03

### Security

* cloudnative-pg: the shared app role loses access to the plausible database now that Plausible uses its own role.

## v1.87.0 - 2026-11-03

### Changed

* plausible: connect to Postgres as its own plausible role instead of the shared app role.

## v1.86.0 - 2026-11-03

### Changed

* cloudnative-pg: plausible gets its own managed role and owns its database; a one-time Job moves its objects off the shared app role.

## v1.85.0 - 2026-11-03

### Changed

* docs: Firefly III login (tnwks-auth through oauth2-proxy) in docs/apps/firefly.md.

## v1.84.0 - 2026-11-03

### Added

* firefly: sign in with tnwks-auth (Cognito) through oauth2-proxy, admins group only; the API keeps token auth on its own ingress.

## v1.83.0 - 2026-11-03

### Changed

* oauth2-proxy: read groups from `cognito:groups`, so ingresses can require a Cognito group with `allowed_groups`.

## v1.82.1 - 2026-11-03

### Fixed

* firefly: set a numeric runAsUser so runAsNonRoot can verify the image user; the pod was stuck in CreateContainerConfigError.

## v1.82.0 - 2026-11-03

### Changed

* firefly: run both containers non-root with no privilege escalation, all capabilities dropped and the RuntimeDefault seccomp profile.

## v1.81.0 - 2026-11-03

### Added

* firefly: Firefly III on the WSL cluster (internal ingress only, pinned 6.5.9, Postgres over TLS on its own role), Passport keypair and app secrets in SOPS, daily cron as a CronJob, attachments in kopiur, docs/apps/firefly.md.

## v1.80.0 - 2026-11-03

### Added

* cloudnative-pg: `firefly` database owned by its own managed `firefly` role (password in SOPS), for Firefly III moving onto the cluster.

## v1.79.0 - 2026-11-02

### Added

* monitoring: RouterDNSCacheNearFull warns when the router DNS cache, which holds the adlists, stays above 80% for an hour, before list imports start failing.

## v1.78.0 - 2026-11-02

### Added

* mikrotik: HaGeZi Threat Intelligence Feeds adlist in subdomain format (~1.94M malware and phishing names), with the DNS cache raised to 320 MiB to hold it.

## v1.77.2 - 2026-11-02

### Fixed

* mikrotik: drop the RPiList Malware and Phishing adlists; they are in adblock syntax, which RouterOS imports (915k names of DNS cache) but never matches.

## v1.77.1 - 2026-11-02

### Fixed

* mikrotik: adlists stopped importing at the 10 MiB DNS cache, so about 4% of blocked names loaded; cache_size is now 192 MiB, three dead lists are dropped, and t.co is allowlisted with a FWD record.

## v1.77.0 - 2026-11-02

### Added

* mikrotik: static DHCP leases on 910 for the devices Home Assistant reaches by address (LIFX, Sonos, Denon, Tesla Wall Connector, Cast, LG TV) and the desktop that hosts homelab-wsl, each at its current address.

## v1.76.0 - 2026-11-02

### Added

* home-assistant: entity states exported to Prometheus (/api/prometheus, ServiceMonitor with a read-only HA user token); docs for presence (GPS + UniFi Wi-Fi trackers) and the metrics.

## v1.75.0 - 2026-11-02

### Added

* unifi: read-only controller account homeassistant for the Home Assistant UniFi integration (Wi-Fi presence); task unifi:bootstrap creates it.

## v1.74.1 - 2026-11-02

### Fixed

* cnpg: PostgresBackupStale fired right after setup because it read cnpg_collector_last_available_backup_timestamp, which stays 0 with the barman-cloud plugin; it now uses barman_cloud_cloudnative_pg_io_last_available_backup_timestamp.

## v1.74.0 - 2026-11-02

### Changed

* docs: first backup restore drill (Postgres recovery, kopiur PVC restore) logged; restore notes for the worker-1-only storage class and first-install placeholder credentials.

## v1.73.3 - 2026-11-02

### Fixed

* kopiur: back up Home Assistant as its own user (568); its .storage has 0600 files.

## v1.73.2 - 2026-11-02

### Fixed

* kopiur: enable credential projection in the chart and allow kopiur-system (Maintenance) on the ClusterRepository; the first snapshots were blocked on both.

## v1.73.1 - 2026-11-02

### Fixed

* backups: the STS refresher could not read its certificate (root-owned 0400 Secret mount); fsGroup + 0440.

## v1.73.0 - 2026-11-02

### Added

* backups: CloudNativePG barman-cloud plugin (continuous WAL + daily base backup to S3, 30-day PITR), kopiur nightly snapshots of the Home Assistant, Mealie, UniFi and changedetection PVCs to S3, and a Roles Anywhere to STS credentials CronJob for both. Daily Aruba SW03 config backup to Google Drive.

### Changed

* docs: backup architecture and decisions (operations/backups.md); disaster-recovery.md rewritten as a BCP with priorities, keys, off-site kit, scenarios and drills.

### Removed

* databases: the MS-01-only Rook RGW cluster-backup path; both clusters use the shared barman-cloud config.

## v1.72.0 - 2026-11-02

### Added

* aws: S3 bucket for homelab backups (versioned, 30-day undo window) and a Roles Anywhere role and profile scoped to it (CN homelab-wsl-backups), for CloudNativePG (barman-cloud) and kopiur. No IAM user.

## v1.71.0 - 2026-11-02

### Added

* wsl-host: tnwks-unifi-backup timer copies the UniFi controller auto-backup to the Google Drive sync folder daily at 02:30 (task wsl-host:unifi-backup).

## v1.70.0 - 2026-11-02

### Changed

* unifi: daily controller auto-backup (02:00, 14 kept) in task unifi:bootstrap; docs on recovering APs without a reset.

## v1.69.0 - 2026-11-02

### Changed

* unifi: AP firmware auto-upgrade on at 03:00 (6.8.2 is available; 6.6.77 was not the last release), and 802.11v BSS transition on for the WLAN.

## v1.68.2 - 2026-11-02

### Fixed

* unifi: adopt the Living Room UAP-HD now that it is reset (managed = true).

## v1.68.1 - 2026-11-02

### Fixed

* unifi: Living Room and Master APs were swapped (the UAP-HD is in the living room); names and MACs follow the rooms, and the not-yet-adopted Living Room AP is held out of Terraform (managed = false) so plans stay clean.

## v1.68.0 - 2026-11-02

### Changed

* unifi: the controller UI at unifi.internal.tnwks.us sits behind the Cognito login (oauth2-proxy); local Terraform reaches the API on the unifi-inform VIP instead. New task unifi:bootstrap does the controller first-run setup over the API, a targeted apply of one AP no longer fails on the module output, and docs/network/unifi.md records the rollout as done.

## v1.67.0 - 2026-11-02

### Added

* unifi: UniFi Network controller on homelab-wsl (networking/unifi, with MongoDB), the LAN path for AP informs (tcp/8080 via the Windows host), a Terraform root for the site, WLAN and the three APs (environments/prod/unifi, modules/unifi), and docs/network/unifi.md with the inferred AP inventory and the adoption runbook.

## v1.66.2 - 2026-11-02

### Fixed

* home-assistant: the wall display tab bar fits on one row again with the Rewards tab (the Home Assistant button is its logo), the Rewards stars fit on phones, and the parent controls stay hidden on the tablet by user agent too.

## v1.66.1 - 2026-11-02

### Fixed

* home-assistant: the rewards scripts return their refusals as a response, so the wall display shows "Not enough stars yet" or "Only a parent can ..." instead of nothing.

## v1.66.0 - 2026-11-02

### Added

* home-assistant: Rewards tab on the wall display. Kids spend stars on rewards; parents define rewards, add or take away stars and undo from their phones only (scripts check the caller). Replaces the reward goal helpers.

## v1.65.0 - 2026-11-02

### Added

* monitoring: Windows host alerts for commit charge near the commit limit (WindowsHostCommitHigh, warning 95% / critical 98%) and sustained paging with low available memory (WindowsHostPaging).

## v1.64.2 - 2026-11-01

### Fixed

* grafana: Windows Exporter NTP offset panel (no data, W32Time counters absent on the desktop) becomes clock skew between Windows and Prometheus.

## v1.64.1 - 2026-11-01

### Fixed

* grafana: Windows Exporter dashboard vendored with queries for windows_exporter 0.31 (memory, processors, RAM and other panels no longer N/A); home memory panel fixed; windows_exporter time collector enabled for the NTP panel.

## v1.64.0 - 2026-11-01

### Changed

* grafana: database moves from SQLite to Postgres (CNPG grafana database); the SQLite file stays on the PVC as a rollback.

## v1.63.0 - 2026-11-01

### Added

* cloudnative-pg: grafana database and its own managed role, for moving Grafana off SQLite.

## v1.62.0 - 2026-11-01

### Added

* grafana: grafana-embed host for frames, signed in only by the kiosk JWT (Viewer); internal ingress allows snippet annotations; cookie_samesite fixed to lax.

## v1.61.0 - 2026-11-01

### Changed

* grafana: new sign-ins get Viewer instead of Admin; home-assistant: the Monitoring dashboard is visible to everyone, not only admins.

## v1.60.0 - 2026-11-01

### Added

* aws: Cognito kiosk user (shared identity for the kitchen wall display) in the viewers group, created without an invitation email.

## v1.59.2 - 2026-11-01

### Fixed

* home-assistant: an overdue robovac part shows as a red "due" tile (a gauge from 0 drew it empty); shorter reset names.

## v1.59.1 - 2026-11-01

### Fixed

* home-assistant: robovac consumable gauges fill with their status color and read "68h 2m" (not "68h 2m h"); reset buttons are compact tiles.

## v1.59.0 - 2026-11-01

### Added

* home-assistant: LIFX theme picker for the pool lights (themes read live from lifx.paint_theme), robovac consumable gauges with reset buttons. Cards 1.9.0.

## v1.58.0 - 2026-11-01

### Changed

* home-assistant: Home dashboard reordered by use (robovac room buttons first), with Robovac and Energy views; pool lights group.

## v1.57.0 - 2026-11-01

### Changed

* mealie: sessions last 30 days (TOKEN_TIME 720) so the wall display's Mealie sign-in survives quiet weeks.

## v1.56.1 - 2026-11-01

### Fixed

* home-assistant: robovac_clean_rooms failed validation (a field example must be a string), which disabled it and the room buttons.

## v1.56.0 - 2026-11-01

### Added

* home-assistant: robovac room scripts (playroom, kitchen, living room by the app's segment ids) with Clean buttons on Home, Sonos on Home; the wall display opens Mealie full screen inside the kiosk. Cards 1.8.0.

## v1.55.0 - 2026-11-01

### Added

* home-assistant: Home gets lights, media, the EV charger and the robovac (map with tap-to-clean rooms); Sonos speakers configured by host.

## v1.54.0 - 2026-11-01

### Changed

* kitchen: the wall display's Home Assistant button goes to the new Home dashboard.

## v1.53.0 - 2026-11-01

### Added

* home-assistant: Home dashboard (/house, the new default), Monitoring dashboard (Grafana in a frame), and the Xiaomi Cloud Map Extractor + vacuum map card installed for the robot vacuum.

## v1.52.0 - 2026-11-01

### Added

* kitchen: the dashboard is now "Wall display", kiosk only on the tablet (by user agent, no ?kiosk needed), a Home Assistant button in the tab bar, Home cards tap through to their pages, event details on tap, and Open in Mealie for meals and the Meals tab. Cards 1.7.0.

## v1.51.0 - 2026-11-01

### Added

* kitchen: tap the weather on the header for details (now, next 12 hours, next days, sunrise/sunset); the shown temperature follows the hourly forecast instead of waiting for the hourly met.no refresh. Cards 1.6.0.

## v1.50.0 - 2026-11-01

### Changed

* kitchen-screensaver: the shuffled pass is remembered between screensaver runs, so every photo shows before any repeats; album changes show up within about 15 minutes (server re-reads every 5, page every 10).

## v1.49.0 - 2026-11-01

### Changed

* kitchen: theme and dark/light mode are per device. The wall display sets the house choice (and its screensaver); phones and browsers keep their own pick, with "Same as kitchen display" to go back. Cards 1.5.0.

## v1.48.4 - 2026-11-01

### Fixed

* kitchen: "Home Assistant menu" and "Back to kitchen" on the wall display reload inside the HA app (homeassistant://navigate) instead of opening Chrome.

## v1.48.3 - 2026-11-01

### Fixed

* kitchen-screensaver: the page's test knobs (interval, reload) only apply together with test=1, so a test URL left in the display's app can't keep it reloading.

## v1.48.2 - 2026-11-01

### Fixed

* kitchen-screensaver: the safety reload works through the ingress: the page has the ImmichFrame app reload it with an image that fails to connect (/api/drop came back as a 502 through nginx, which the app ignores).

## v1.48.1 - 2026-11-01

### Fixed

* kitchen-screensaver: the 6-hourly safety reload no longer uses location.reload(), which the ImmichFrame app treats as a navigation and crashes its screensaver on; the page now has the app reload it (a deliberately failed request to /api/drop). /favicon.ico answers 204.

## v1.48.0 - 2026-11-01

### Added

* Kitchen display: dark, light or auto (by the sun) mode from the 🎨 sheet (input_select.kitchen_mode), with a light palette for every theme and the Android status and navigation bars following it on the wall display; a "Home Assistant menu" button there that opens HA's menu with ?disable_km, and a "Back to kitchen" pill (or 5 idle minutes) to return to the kiosk.

## v1.47.0 - 2026-11-01

### Added

* kitchen-screensaver: our own page for the kitchen display's screensaver, with face-aware photo crops, stacked landscape pairs, and Home Assistant weather, events (all-day handled), dinner, stars and the house theme; Immich previews raised to 2160 px for the 4K panel. ImmichFrame stays as the fallback.

## v1.46.0 - 2026-11-01

### Added

* ImmichFrame on homelab-wsl, the kitchen display's Android screensaver - Immich's "Kitchen screensaver" album with a 12-hour clock and today's family events, behind an auth secret (docs/apps/immichframe.md).

## v1.45.0 - 2026-11-01

### Added

* Hourly Immich sync for the kitchen screensaver, mirroring the Google Photos shared albums on Home Assistant's "Screensaver albums" list (originals, photos only) into per-album and combined Immich albums.

## v1.44.0 - 2026-11-01

### Added

* Immich photo library on homelab-wsl (`immich.internal.tnwks.us`, tnwks-auth login), the photo store for the kitchen screensaver.

## v1.43.0 - 2026-11-01

### Added

* Cognito app client `immich` so Immich can log people in with tnwks-auth through its built-in OAuth.

## v1.42.1 - 2026-10-31

### Fixed

* Kitchen display: holiday offer answers no longer keep the "unknown" placeholder, and routine rows are a little shorter so a long Saturday fits

## v1.42.0 - 2026-10-31

### Changed

* Kitchen display: tabs switch instantly (one HA view with every tab kept mounted), taps and check-offs animate, routines can be added, edited and deleted on the display (they moved from input_booleans into the todo.kitchen_routines list), and holiday themes with an offer on Home and a 🎨 picker

## v1.41.0 - 2026-10-31

### Added

* Kitchen display: the family calendar (Remote Calendar on the Google calendar's secret iCal feed) refreshes every 15 minutes instead of Remote Calendar's daily poll

## v1.40.2 - 2026-10-31

### Fixed

* Mealie: OIDC login asks Cognito only for openid email profile (it rejected cognito:groups as a scope) and takes the display name from the email claim

## v1.40.1 - 2026-10-31

### Fixed

* Home Assistant: the home location moved about 190 m to where the phones actually sit at night, so people at home read as home instead of away

## v1.40.0 - 2026-10-31

### Added

* Mealie: log in with tnwks-auth (Cognito) through Mealie's built-in OIDC; admins become Mealie admins, viewers users, anyone else is refused. Password login stays for the break-glass admin

## v1.39.0 - 2026-10-31

### Added

* Cognito: a mealie app client (confidential, authorization code) for Mealie's built-in OIDC login, with its client ID and secret as outputs

## v1.38.0 - 2026-10-31

### Added

* Kitchen display: closer to the Apolosign app. Calendar tab with Family, Week, Month and List views, day/week/month paging and a person filter; a Meals tab (week grid from the Mealie calendars); a Lists tab with several lists, an add box, tap to check off and a Completed section; sticky notes (todo.notes) on Home and Lists; a greeting line with chores left, events and groceries; labelled routine periods and stars earned today

### Fixed

* Kitchen display: the cards reload the page when HA serves a new version of them, so a wall display or phone that kept the old code after a restart no longer crashes on a new dashboard config. A card that hits an error shows it in place instead of blanking

## v1.37.0 - 2026-10-31

### Added

* Privacy check covers commit messages (commit-msg hook) and your git identity at commit time, re-checks PR titles and bodies on edit, and deletes GitHub comments or replaces review bodies that contain a denylisted term (privacy-text.yaml)

## v1.36.0 - 2026-10-31

### Changed

* Privacy check now blocks: required in the main ruleset and run by a pre-commit hook. Exceptions live in .github/privacy-allow (name-free globs) or a privacy:allow line comment; the storefront app is the first

## v1.35.0 - 2026-10-31

### Changed

* Kitchen display: members use neutral keys (adult1, adult2, kid1, kid2) in entity IDs, and display names come from the home-assistant Secret, so no family names are in the repo. Stars carry over through the counters' initial values

## v1.34.0 - 2026-10-31

### Changed

* Renovate approval label is now owner/approved (was named after a person); docs and the review workflow say "the owner". Drops a commented-out Frigate camera

## v1.33.0 - 2026-10-31

### Added

* Privacy check: bin/privacy-check flags family names and personal emails from a SOPS-encrypted denylist on every PR (CI reads it from the PRIVACY_TERMS secret); report-only until the existing hits are cleaned up

## v1.32.0 - 2026-10-31

### Changed

* litellm: v1.86.2 -> v1.106.6. Runs new Prisma migrations on start; the master key already meets v1.104's refuse-to-start check

## v1.31.1 - 2026-10-31

### Fixed

* litellm: pin the image to v1.86.2 with its digest instead of the rolling main-stable tag, so Renovate can track it; Renovate follows only plain vX.Y.Z releases and LiteLLM minors are reviewed, not automerged

## v1.31.0 - 2026-10-31

### Added

* home-assistant: Kitchen dashboard for the Apolosign wall display, with chores and stars (routines package), our own frontend cards served from a ConfigMap, and kiosk-mode

## v1.30.3 - 2026-10-31

### Fixed

* Flux manages its own install on WSL: kubernetes/clusters/wsl includes ./flux-system, so a merged Flux upgrade (gotk-components.yaml) rolls out on reconcile instead of needing a manual apply

## v1.30.2 - 2026-10-31

### Fixed

* k8s-gateway moves to the k8s-gateway project chart 4.0.1 on Codeberg (image codeberg.org/k8s-gateway/k8s_gateway 1.9.2). The old quay.io/oriedge/k8s_gateway image is gone, so the pod only ran from worker-1's image cache

## v1.30.1 - 2026-10-31

### Fixed

* WSL kubelets delete images no container has used for a week (imageMaximumGCAge 168h). Disk-based image GC never ran because the node filesystem is the 1 TB WSL disk, so old images piled up (124G across the workers) and filled E:

## v1.30.0 - 2026-10-31

### Changed

* Flux Alert and Provider for the War Room move from notification.toolkit.fluxcd.io/v1beta3 to v1 (deprecated in Flux 2.10)

## v1.29.0 - 2026-10-31

### Changed

* WSL cluster on Talos 1.13.12 (from 1.13.2), rolled node by node with `task talos:upgrade:wsl`

## v1.28.0 - 2026-10-31

### Changed

* WSL cluster on Kubernetes 1.35.10 (from 1.34.0), rolled out by tuppr; Flux 2.10 needs 1.35.2 or newer

## v1.27.0 - 2026-10-31

### Added

* Cluster upgrades through Renovate: tuppr (system-upgrade) rolls out Kubernetes versions from a KubernetesUpgrade on merge, MS-01 also gets a TalosUpgrade, and the WSL Talos version is pinned in talos/wsl/versions.yaml and rolled with `task talos:upgrade:wsl`, which swaps each node container onto the new image with its volumes. Renovate groups each version across files with one PR per minor (docs/operations/cluster-upgrades.md)

### Fixed

* The worker-2 augmentation recreate in 01-talos-bootstrap.yml keeps the container's memory and CPU limits instead of resetting memory to 8G

## v1.26.0 - 2026-10-31

### Changed

* playwright-mcp runs Microsoft's official image (mcr.microsoft.com/playwright/mcp v0.0.83) instead of the Acuvity rebuild, which lagged upstream by ~27 releases and published its Helm chart as image tag 1.0.0

## v1.25.1 - 2026-10-31

### Fixed

* CI: the image diff renders each cluster from its own entrypoint, so PRs that touch frigate no longer fail on an unsubstituted ${SERVICE_MONITOR_ENABLED}; the live WSL cluster also gets its own render test. Image exists rejects tags that are not container images (Helm charts pushed into an image repo), and a registry rate limit on a Renovate PR warns instead of failing

## v1.25.0 - 2026-10-29

### Changed

* Renovate: updates that only touch MS-01 manifests or Talos config (Cilium, Rook Ceph, Thanos, Vector agent, UISP, node-feature-discovery, CNPG backups) automerge behind Build Success while MS-01 is not live, instead of going to review

## v1.24.0 - 2026-10-29

### Changed

* Renovate review: Ori now migrates and lands impact/high PRs instead of leaving majors open, and a new impact/critical tier (Flux, cluster version, CNI, data store majors) merges only after the owner adds owner/approved; ARC controller and scale sets are grouped, and image-only bumps of the generated Flux manifest are off

## v1.23.0 - 2026-10-28

### Added

* Renovate review workflow: impact/high Renovate PRs go straight to Ori's reviewer agent through an OpenClaw hook on a new openclaw-hooks runner, with ori/blocked tracking what was left open

## v1.22.1 - 2026-10-28

### Fixed

* Flux failure alerts cover HelmReleases in every app namespace (an Alert event source only matches one namespace); CI fails if a namespace with a HelmRelease is left out

## v1.22.0 - 2026-10-28

### Added

* Renovate tiers: app patches (and non-0.x minors, weekly) automerge once the new Kubernetes CI passes (flate render + image exists); platform, Terraform, majors, 0.x minors and stateful-app minors get impact/high and are never automerged. Flux error events now page the War Room through Alertmanager

## v1.21.0 - 2026-10-28

### Changed

* Cloudflare Terraform moved to provider v5 (cloudflare_dns_record, per-setting cloudflare_zone_setting, list-style ruleset rules); state migrated with moved/import blocks, Cloudflare itself unchanged

## v1.20.0 - 2026-10-27

### Added

* "WAN Traffic" Grafana dashboard: live and historical WAN usage per device, by app, category and destination network, from the router's NetFlow; flow-collector now labels each flow with service, app and category from its port and remote network

### Fixed

* Per-client WAN rates (the "Top users" line in WAN alerts) are no longer inflated ~80000x; since the mktxp switch they were multiplied by each DHCP lease's seconds-to-expiry

## v1.19.1 - 2026-10-27

### Fixed

* WAN download usage per client is counted again; since the RB5009 cutover every download flow carried the router's public address and was dropped, so the "Top users" download line in WAN alerts was empty

## v1.19.0 - 2026-10-27

### Added

* Prometheus scrapes Frigate, and the War Room gets an alert when Frigate's object detection stalls, every camera feed is down, or Frigate stops answering; a hung Coral detector left person alerts off for 16 minutes on 2026-10-26 with every pod Running

## v1.18.4 - 2026-10-27

### Fixed

* The LAN bridge's Scheduled Task re-syncs the portproxy rules after a WSL vNIC reset; its VmSwitch trigger listened on a log that never gets EventID 232, and it ran as SYSTEM, which can't see the WSL distro

## v1.18.3 - 2026-10-26

### Security

* cloudflared runs 2026.10.4; versions before 2026.10.2 still trust the eight eMudhra (emSign) intermediates with weak private keys, so anyone holding those keys could impersonate the Cloudflare edge to the tunnel

## v1.18.2 - 2026-10-26

### Security

* Frigate's LAN UI requires an SSO login (oauth2-proxy) and redirects plain http to https; it used to serve its unauthenticated API to the whole LAN, camera credentials included

## v1.18.1 - 2026-10-26

### Fixed

* Home Assistant's login works over a port-forward (it no longer forces https, which sent http://localhost:8123 to https), and its welcome screen offers both SSO and the local login

## v1.18.0 - 2026-10-26

### Added

* Windows host monitoring agents as code: task wsl-host:monitoring installs windows_exporter, smartmontools and smartctl_exporter at pinned, checksummed versions and limits both exporters to the WSL subnet

## v1.17.2 - 2026-10-26

### Fixed

* Public apps see real visitor IPs; the public ingress-nginx trusts cloudflared's X-Forwarded-For from the pod network instead of reporting every visitor as the cloudflared pod

## v1.17.1 - 2026-10-26

### Fixed

* Frigate runs object detection on the Coral again; detect.enabled defaults to false, so no camera was sending it frames and no person alerts or alert recordings were being produced

## v1.17.0 - 2026-10-26

### Added

* Home Assistant runs on homelab-wsl (2026.10, was pinned at 2025.3) at hass.tnwks.us and hass.internal.tnwks.us with Cognito SSO, the Frigate integration, a Security camera dashboard and night-time person alerts as Companion app pushes that break through Do Not Disturb; its config lives in the repo and CI runs check_config on it

## v1.16.0 - 2026-10-26

### Added

* SMART disk-health alerts for the Windows host (smartctl_exporter + Win32_DiskDrive), and the Windows memory alerts work again

## v1.15.0 - 2026-10-26

### Added

* Cognito app client for Home Assistant SSO (public, PKCE) with managed login branding

## v1.14.1 - 2026-10-26

### Fixed

* Mosquitto persists its database again; the PVC was mounted at /data while the broker saved to the read-only /mosquitto/data, so retained messages were lost on restart

## v1.14.0 - 2026-10-26

### Added

* WSL Talos break-glass: a script that mints an admin talosconfig from the cluster's CA, and a SOPS-encrypted copy in the repo

## v1.13.3 - 2026-10-26

### Fixed

* WSL Talos: finished pods are garbage-collected (terminated-pod-gc-threshold 1), so leftovers from node restarts can no longer stall Recreate rollouts

## v1.13.2 - 2026-10-26

### Fixed

* Grafana restarts once per config change (Reloader no longer races the chart's own restart), which had left it with no pod

## v1.13.1 - 2026-10-26

### Fixed

* Grafana shows one Home dashboard (a stale provisioned duplicate is gone)

## v1.13.0 - 2026-10-26

### Removed

* Grafana: retired dashboards (Ceph, node-exporter, speedtest); the MikroTik dashboard moves to a Network folder

## v1.12.1 - 2026-10-26

### Security

* Router no longer routes the retired EdgeRouter transit VLAN 10 (172.16.1.0/24), so no VLAN can reach a device left there; the ERL fallback route is gone

## v1.12.0 - 2026-10-26

### Added

* EdgeOS devices plan on PRs and apply on merge from the self-hosted runner, like the MikroTik root

## v1.11.0 - 2026-10-26

### Changed

* The CI runner's tunnel also forwards the EP-R6's SSH, and the runner carries an SSH key for EdgeOS devices

## v1.10.0 - 2026-10-26

### Added

* The CI runner's wg03 tunnel can reach the EP-R6's SSH (and nothing else on VLAN 140)

## v1.9.0 - 2026-10-26

### Changed

* EP-R6: hostname sce-ep01, its drop-invalid firewall rules drop, NTP from the router, the airCube gets public DNS for the clients behind it, and the CI runner's SSH key

## v1.8.2 - 2026-10-26

### Fixed

* edgeos-sync applies over SSH: commit-confirm without EdgeOS's interactive prompt, and the confirm step checks the rollback job is really cancelled

## v1.8.1 - 2026-10-26

### Fixed

* EP-R6 DHCP clients, DNS forwarder and resolver use the router (10.10.140.1) instead of the retired VyOS DNS container

## v1.8.0 - 2026-10-26

### Added

* EP-R6 configuration as code: EdgeOS set commands in infrastructure/edgeos with a plan/apply tool (device-validated plans, commit-confirm applies)

## v1.7.0 - 2026-10-26

### Added

* MikroTik changes plan on PRs and apply on merge from a self-hosted runner (tnwks-ops-runner) that reaches only the router's REST API over wg03

## v1.6.0 - 2026-10-26

### Added

* Router wg03 listener for the CI runner: its own zone, REST API only, joined from the LAN; the MikroTik secrets gain a CI-only age recipient

## v1.5.3 - 2026-10-26

### Security

* The four age-only SOPS files (MikroTik secrets, the Roles Anywhere CA key, the mktxp and storefront API Secrets) gain the KMS recipient, so they no longer depend on the age keys alone

## v1.5.2 - 2026-10-26

### Fixed

* Roles Anywhere SOPS sessions can pass their session tags to iam-role-sops (sts:TagSession)

## v1.5.1 - 2026-10-26

### Fixed

* Roles Anywhere SOPS sessions can assume iam-role-sops (sts:SetSourceIdentity), and tasks find the SOPS age key

## v1.5.0 - 2026-10-26

### Added

* IAM Roles Anywhere for workstation SOPS KMS access: a self-managed CA, a prod-account role that can only assume iam-role-sops, and aws:ra-* tasks

## v1.4.1 - 2026-10-26

### Fixed

* Router syslog reaches Loki: sent over UDP, since RouterOS TCP syslog has no message framing; the Windows and WSL relay setup is now task wsl-host:lan-bridge and task wsl-host:udp-relays

## v1.4.0 - 2026-10-26

### Added

* Router syslog to Loki: vector-aggregator on the WSL cluster, a tcp/6001 LAN bridge, and RouterOS BSD-syslog output

## v1.3.0 - 2026-10-26

### Added

* Router monitoring via mktxp: RouterOS metrics, router alerts, a MikroTik dashboard, and WAN throughput and client names from the router

### Removed

* VyOS and EdgeRouter scrape jobs, alerts and dashboards, and the EdgeRouter smokeping target

## v1.2.7 - 2026-10-26

### Fixed

* Router DNS drops records for retired hosts and the absent UISP server, and adds sce-rtr01

## v1.2.6 - 2026-10-26

### Fixed

* Router DDNS updates the Cloudflare record the WireGuard clients use as their endpoint; Terraform reaches the router over wg01

## v1.2.5 - 2026-10-26

### Fixed

* Flow export uses NetFlow v9: the routeros provider only accepts IPFIX in a spelling RouterOS 7.25 rejects

## v1.2.4 - 2026-10-26

### Fixed

* Flow export target uses RouterOS's lowercase ipfix version value

## v1.2.3 - 2026-10-26

### Fixed

* MikroTik config matches how RouterOS stores values (durations, single-host addresses, booleans, DHCP lease identifiers), so plans after an apply are empty

## v1.2.2 - 2026-10-26

### Fixed

* MikroTik module applies cleanly on RouterOS 7.25: NAT and flow export wait for VLAN interfaces, log prefixes fit 31 characters, the logging action name is alphanumeric, and the removed SSH password option is no longer set

## v1.2.1 - 2026-10-26

### Fixed

* RouterOS bootstrap waits for the API certificate key instead of a fixed 5 s pause

## v1.2.0 - 2026-10-26

### Changed

* Terraform CI: least-privilege tokens, applies gated on the prod environment, PR gating on author and source repo

### Fixed

* Release workflow no longer fails when another merge lands on main while it runs

## v1.1.0 - 2026-10-26

### Added

* Terraform port of the VyOS router and the EdgeRouter onto a MikroTik RB5009: zone firewall, admin-VPN-only management, staged cutover checklist and break-glass docs

## v1.0.0 - 2026-10-26

### Added

* Changie-driven changelog and releases: each PR adds a fragment, and merging to main cuts the version, tag and GitHub Release
