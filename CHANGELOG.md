# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

## v2.100.1 - 2026-11-13

### Fixed

* agents: the providers job of the workflow agents runs again after its first run. It ran `openshell profile lint`, which refuses a profile that is already in the gateway, so no later run updated the GitHub token and the reviewer got 401 after one hour. It now imports a new profile, updates a changed one (by the sha256 of its file), checks the gateway and the profiles before it asks for a token, and logs one line for each failed step

## v2.100.0 - 2026-11-13

### Added

* monitoring: three alerts for the room on homelab-wsl-02, each with a promtool fixture (NodeContainerMemoryNearLimit: worker 1 past 90% of the 18 GiB limit of its node container, from the kubelet resource endpoint scraped there; LokiSecurityIngestAbovePlan: the 90-day streams of its Loki above twice their plan of 1.2 GB of lines a day; WslDriveSpaceLow: under 100 GB free on the drive of its WSL disk)

## v2.99.0 - 2026-11-13

### Changed

* monitoring: the Loki of homelab-wsl-02 keeps a stream with the label security_stream for 90 days (the other streams stay at 14 days), and its ServiceMonitor is on with an interval of 1 minute; the rates of loki.rules read 5 minutes, and LokiRequestLatency leaves out the querier loop, as the Loki mixin does

## v2.98.0 - 2026-11-13

### Changed

* wsl-02: worker 1 of homelab-wsl-02 has a memory limit of 18 GiB (talos_clusters memory_nodes; task talos:memory:wsl-02 sets it on the running node container, with no restart), and the WSL VM of WSL-02 has 45 GB (wsl_memory in the role windows_host_settings, read at the next WSL restart)

## v2.97.0 - 2026-11-13

### Added

* security: the namespace security on homelab-wsl and homelab-wsl-02 (baseline Pod Security, a network policy that denies by default), SECURITY_NODE in each cluster-settings for the node selector of its pods, and the contract for its apps in kubernetes/apps/security/README.md

## v2.96.1 - 2026-11-13

### Fixed

* agents: the run receiver keeps the answer of a run in an annotation of its Job, so a second call with the same key gets it after the pod GC deleted the run pod (workflow agents 0.1.2, and jobs list and patch in its Role)

## v2.96.0 - 2026-11-13

### Added

* agents: the providers of the Renovate reviewer in agents-workflows (the model gateway on its Messages API, and GitHub on iT3E/tnwks-ops only through an installation token of its own GitHub App, scoped to that repository and to the permissions listed in git, with a drift check of the App), the job that writes them into the sandbox gateway every 15 minutes, and the reviewer 0.1.1 on the Messages API with the effort high

## v2.95.0 - 2026-11-13

### Removed

* agents: the message hold plugin of the pilot, as a sandboxed session has no message tool for it to hold

## v2.94.1 - 2026-11-13

### Fixed

* agents: the message hold of the pilot loads from a copy in a read-only plugins volume, as the product refuses a plugin in a directory that others can write

## v2.94.0 - 2026-11-13

### Added

* agents: the pilot agent asks the owner before it sends a message to an address that it names

## v2.93.1 - 2026-11-13

### Fixed

* agents: the memory request of the pilot agent is 2 GiB and that of the model bridge is 512 MiB, as the pilot measured

## v2.93.0 - 2026-11-13

### Added

* agents: the run receiver of the workflow agents in agents-workflows, with its role for Jobs only, an admission policy that admits only a Job from the template of an agent, its network policy and the sweep of old run sandboxes

## v2.92.2 - 2026-11-13

### Fixed

* agents: the model entry of the pilot names its Claude model, so the product keeps the context of each turn in the history, the model bridge matches the conversation at the next turn, and the prompt cache works

## v2.92.1 - 2026-11-13

### Fixed

* agents: the pilot reads its model gateway key by an explicit env SecretRef, so the doctor no longer reports it as plaintext, and the pilot's tool to read and change its own config is denied

## v2.92.0 - 2026-11-13

### Added

* WSL-02 can't sleep any more: a new windows_power role disallows standby on its power scheme and turns hibernation off, after three sleeps in two weeks took homelab-wsl-02 down (95 minutes on 2026-11-12)

## v2.91.4 - 2026-11-13

### Fixed

* alerts: a UniFi controller restart no longer reads as every AP rebooting, and the desktop's nightly drift check accepts its Wi-Fi card disabled in Device Manager and the syslog relay rule's stored address

## v2.91.3 - 2026-11-12

### Fixed

* model-bridge: a stop of the pod lets the calls in work end. A preStop sleep of 5 s, BRIDGE_DRAIN_SECONDS 75 and a grace period of 90 s, and a rollout that starts the new pod first

## v2.91.2 - 2026-11-12

### Fixed

* agents: the pilot agent starts each of its sandboxes that the sandbox gateway left stopped, when the pod starts and then each minute

## v2.91.1 - 2026-11-12

### Fixed

* agents: the daily backup of the pilot agent runs in the PID namespace of its gateway, after the gateway is ready; the agent fetches no hosted model catalog and no helper binaries

## v2.91.0 - 2026-11-12

### Added

* tools: a runner scale set for the checks of a private docs repo, with no Docker

## v2.90.1 - 2026-11-12

### Fixed

* agents: the backup movers of agents-pilot reach the dual-stack S3 endpoint that Kopia uses
* agents: the pilot agent and its Signal daemon mount each state volume at its root, with no subPath; the product finds its state and config through OPENCLAW_STATE_DIR and OPENCLAW_CONFIG_PATH, and treats the config as read-only

## v2.90.0 - 2026-11-12

### Added

* agents: the pilot agent in agents-pilot (OpenClaw in the agent image, with the OpenShell sandbox backend and one model at the model gateway) on Telegram, its Signal daemon (off until the test number exists), the archive step and daily backup of each volume, the check of the instruction files, and a kopiur policy for each volume

## v2.89.0 - 2026-11-12

### Changed

* LiteLLM: the names of the agent platform (agents-main, agents-finance, agents-workflows, agents-pilot), each pointing at one entry by one line of model_group_alias; Claude Opus through the model bridge with the bridge key of each trust domain, and Claude Opus on Bedrock through the Invoke API; no fallback. The claude-opus-plan entry and its credential are gone

## v2.88.2 - 2026-11-12

### Fixed

* Agent platform: the sandbox workload gets no DNS egress (allow-dns selects no sandbox pod), and the sandbox probe checks that the cluster DNS Service gives it no answer over UDP or TCP

## v2.88.1 - 2026-11-12

### Fixed

* Agent platform probe: find the two pods of the probe sandbox through its Sandbox record while it runs, require both running, and log each pod with its node

## v2.88.0 - 2026-11-12

### Added

* The model bridge of the agent platform in the ai namespace on homelab-wsl: one replica with the restricted settings and a read-only root file system, the Service model-bridge on port 8080, and a network policy that lets in the model gateway pods only and lets out the API of the vendor only

## v2.87.0 - 2026-11-12

### Changed

* Frigate: the east camera is now the EmpireTech frigate_empiretech_east (was frigate_sv3c_east, dead since 2026-09), enabled with audio; MikroTik: its temporary factory-default access is removed

## v2.86.0 - 2026-11-12

### Changed

* MikroTik: the 10.10.60.35 reservation moves from the dead SV3C (sv3c02) to the replacement EmpireTech camera (empiretech01)

## v2.85.0 - 2026-11-12

### Added

* MikroTik: VLANs take extra_addresses; temporary 192.168.1.1/24 on seccam-610 and HTTP from the desktop to 192.168.1.108, to set up the replacement east camera at its factory default

## v2.84.0 - 2026-11-11

### Added

* LiteLLM: Claude Opus on the Anthropic API as the model claude-opus-plan, with its credential from litellm-secret

## v2.83.0 - 2026-11-11

### Added

* Add daily checks to each agent trust domain on homelab-wsl: one tests that only its own sandbox gateway answers it, and one probes the network of a new sandbox.

## v2.82.0 - 2026-11-11

### Added

* Alloy: an OTLP/HTTP receiver for the agent platform's traces, with a filter that removes the text attributes of a conversation before Tempo, and a daily test span (trace-filter-check)

## v2.81.0 - 2026-11-11

### Added

* Run one OpenShell sandbox gateway in each agent trust domain on homelab-wsl, with certificates from cert-manager and a network policy that lets in its own namespace only.

## v2.80.0 - 2026-11-11

### Added

* LiteLLM: a key in keys.json can set a spend limit (max_budget, budget_duration), and the agent platform has one key alias for each trust domain

## v2.79.0 - 2026-11-11

### Added

* CI: a disposable Alloy rehearsal runs the pinned Alloy with the monitoring HelmRelease's config and checks that every component is healthy

## v2.78.0 - 2026-11-11

### Added

* Add the agent platform's trust-domain namespaces on homelab-wsl (agents-main, agents-finance, agents-workflows and agents-pilot), each with the restricted Pod Security level and a network policy that denies by default.

## v2.77.1 - 2026-11-11

### Fixed

* Agent Sandbox controller: remove the chart's helm.sh/chart and app.kubernetes.io/version labels after render, so its objects install with the chart built per tag and the image pinned by digest

## v2.77.0 - 2026-11-11

### Added

* Run the Agent Sandbox controller on homelab-wsl, in its own namespace with the restricted Pod Security level, as the first part of the agent platform.

## v2.76.1 - 2026-11-11

### Fixed

* Renovate bumps Grafana's image digest, the image renderer and the observability version pins together, so a Grafana update no longer keeps the old digest or fails the observability contract

## v2.76.0 - 2026-11-11

### Changed

* changedetection alerts post straight to the War Room as the Alertmanager bot, with no LLM summarizer in the path

## v2.75.4 - 2026-11-10

### Fixed

* Household cameras state counts distinct cameras, so a Frigate pod with a new IP no longer shows Degraded for a day

## v2.75.3 - 2026-11-10

### Fixed

* MikroTik DNS: allowlist click.redditmail.com and its CNAME target thirdparty.bnc.lt so links in Reddit emails resolve

## v2.75.2 - 2026-11-10

### Fixed

* The Windows host apply stops the Coral detectors before it replaces a DLL they have loaded, so a new libusb shim (or Edge TPU runtime) no longer fails with "being used by another process".

## v2.75.1 - 2026-11-10

### Fixed

* Home Assistant: the camera card no longer breaks after a version bump. Its resource URL gets a ?v= cache-buster, and Renovate moves ?v= with the version for every installed card (camera, kiosk mode, vacuum map).

## v2.75.0 - 2026-11-10

### Changed

* The Coral detector's libusb shim builds on Ubuntu 26.04 with its mingw-w64 packages (binutils 2.45.90, mingw-w64 13.0.0); the shim's pinned hash moves with it.

## v2.74.0 - 2026-11-10

### Changed

* Kubernetes CI renders each live cluster from its own entrypoint and no longer renders the whole tree, which flate 0.7 refuses for this layout; flate moves to 0.7.3.

## v2.73.1 - 2026-11-10

### Fixed

* Pod logs in Loki carry the app label again (vector-aggregator read pod labels from the wrong path, so every stream was app="unknown"), and the repeated label maps are dropped from each line.

## v2.73.0 - 2026-11-10

### Added

* Job pods' logs go to Loki on both WSL clusters (vector-agent with a Job-only pod selector), so a failed CronJob's output outlives its pod; homelab-wsl-02 gets its own Loki and vector-aggregator.

## v2.72.2 - 2026-11-10

### Fixed

* KopiurBackupStale no longer fires on a Kopiur policy that no schedule uses on any site (unifi-mongo, which only moves a PVC in a site failover): the observability collector reports it as unscheduled.

## v2.72.1 - 2026-11-09

### Fixed

* Migrate the healthcheck CEL imports to cel.dev/cel-go for the upstream module rename.

## v2.72.0 - 2026-11-09

### Changed

* Groceries on the wall display is a Local To-do list, so its items take notes and expiry dates, and every list that can store dates offers a date in the item editor. The old Shopping List integration list stays in HA but leaves the Lists tab.

## v2.71.1 - 2026-11-09

### Fixed

* The wall display's Lists and Notes buttons no longer balloon when touched (rows stretched while scrolling, Add grew huge), and tapping the open list's tab opens its settings.

## v2.71.0 - 2026-11-09

### Added

* Edit, reorder and delete items on the wall display's Lists tab, give a list expiry dates, and make, rename and delete lists from the display (kitchen_lists integration).

## v2.70.0 - 2026-11-09

### Added

* Show statement-backed solar credits and checked billing-period grid energy on the Home energy tab.

## v2.69.1 - 2026-11-09

### Fixed

* mealie: escape the nginx variable in mealie-embed's snippet so Flux's substitution doesn't reject the Ingress

## v2.69.0 - 2026-11-09

### Changed

* mealie: mealie-embed host for the wall display's frame, signed in as the kiosk identity (an OIDC-created Mealie user) with its API token; HA's Mealie frame uses it instead of the local kitchen user

## v2.68.1 - 2026-11-08

### Fixed

* Terraform deploys: the Cloudflare, UniFi, MikroTik and Healthchecks applies no longer fail at job setup on the self-hosted runners. The Terraform Cloud apply steps moved into a local action, so the job no longer pulls their Docker image on runners without Docker.

## v2.68.0 - 2026-11-08

### Changed

* CI: setup-flate (with flate-guard), the Terraform plan/apply action, the sticky PR comment, zizmor and the workflow conventions check come from iT3E/github-workflows; the local copies are gone. Every github-workflows pin moves to the same release.

## v2.67.0 - 2026-11-08

### Changed

* CI: CodeQL moves to an advanced-setup workflow that scans Python and the workflows on changes that touch them, and weekly on main, instead of seven languages on every PR and push.

## v2.66.1 - 2026-11-08

### Fixed

* CI: the Home Assistant check caches the installed custom integrations by pinned version, so GitHub archive and release outages no longer fail it unless a version changed.

## v2.66.0 - 2026-11-08

### Changed

* CI: jobs that only call the GitHub API or run a few seconds of jq or Python (Changes, Build Success, conformance, Renovate review Select, compatibility notify) run on ubuntu-slim.

## v2.65.1 - 2026-11-08

### Fixed

* CI: Deploy starts on a push to main (its Terraform caller now grants what terraform.yaml's plan jobs declare), and CI no longer plans Terraform on pushes to main, where every root waited on the approval environment.

## v2.65.0 - 2026-11-08

### Changed

* CI: Terraform plans for every root run from ci.yaml as one matrix and gate Build Success; applies run from the new deploy.yaml. Adding a root is one entry in .github/ci/domains.toml

## v2.64.0 - 2026-11-08

### Added

* CI: a nightly bootstrap smoke boots a throwaway Talos-in-Docker cluster, installs Flux from main and waits for every Flux source to be Ready

### Changed

* Renovate: third-party container digest updates open on Mondays only, instead of one PR and CI run per re-pushed tag

## v2.63.0 - 2026-11-08

### Added

* CI: a nightly CI stats report (runs a day, CI time to result and flake rate, cache usage, stuck runs) that fails when the flake rate passes 2%, the cache passes 9 GB, or a run waits over an hour

### Fixed

* CI stats: 20-minute timeout (a week of runs takes about 5 minutes to page) and lighter run pages

## v2.62.1 - 2026-11-08

### Fixed

* Runners: homelab-wsl's ops runner registers as tnwks-ops-runner-homelab-wsl with both of its labels, so jobs that need the homelab-wsl site (UniFi plans and applies) get a runner again; the labels added in place never reached GitHub.

## v2.62.0 - 2026-11-08

### Changed

* CI: one Terraform action (.github/actions/terraform) replaces the runner, MikroTik and UniFi copies; endpoint overrides come in as tf-vars, and every root's plan summary lists moves.

## v2.61.0 - 2026-11-08

### Changed

* changedetection: UI behind tnwks-auth through oauth2-proxy (admins only), /api/v1 on its own Ingress for API-key clients, docs/apps/changedetection.md (watches and the alert path).

## v2.60.1 - 2026-11-08

### Fixed

* Use the maintained Pacific Power integration for usage and grid exports; keep banked-kWh credits as a PDF import and remove the unverified billing client.

## v2.60.0 - 2026-11-08

### Added

* Grafana grades each site's WSL VM next to its Windows host (WSL CPU, memory, disk space and I/O stall on the Hosts tile and Host state rows), adds a WSL VM tab to Host (CPU, memory, swap and disk against the .wslconfig limits, and pressure stall) and WSL VM CPU and memory graphs to Hosts & Capacity

## v2.59.0 - 2026-11-08

### Changed

* CI: one entrypoint, ci.yaml. bin/ci-changes picks the domains a change touches (.github/ci/domains.toml), each domain's workflow runs through workflow_call, and Build Success needs all of them, so Observability, Home Assistant, Ansible lint, security regressions and the workflow linters now gate merges too. A conformance check enforces timeouts, runner labels, SHA pins and ci.yaml coverage.

## v2.58.1 - 2026-11-08

### Fixed

* Read Pacific Power statement dates from the billing account-info endpoint and report the failed setup stage without exposing account data.

## v2.58.0 - 2026-11-08

### Added

* CI: Kubernetes PRs get one sticky comment with the rendered manifest diff (flate diff against the base branch) for each live cluster; flate is installed through a shared setup-flate action.

## v2.57.0 - 2026-11-08

### Changed

* Ansible: the Coral libusb shim builds in a pinned toolchain image (digest-pinned Ubuntu 24.04, mingw-w64 from a fixed snapshot), so the controller needs Docker instead of mingw-w64, and the Ansible Hosts plan job runs on ubuntu-26.04.
* CI: Observability runs its Garage/Thanos and Grafana browser rehearsals in a separate job, only when observability/, kubernetes/apps/monitoring/ or the workflow changed; other Kubernetes PRs get the fast contract checks only.

## v2.56.0 - 2026-11-08

### Changed

* CI: same-repo Renovate PRs get Terraform plans instead of waiting for approval; Terraform providers, modules and the CLI wait 3 days after release before Renovate opens a PR.

## v2.55.0 - 2026-11-08

### Changed

* CI: zizmor audits the workflows and local actions on PRs and main; the edgeos-sync action passes its mode through env instead of template expansion.

## v2.54.0 - 2026-11-08

### Added

* Add a Home Assistant integration for Pacific Power banked solar kWh, with statement dates, expiry billing months, PDF import and automatic statement checks.

## v2.53.0 - 2026-11-08

### Changed

* CI: hosted jobs run on ubuntu-26.04, every job has a timeout, superseded PR runs cancel in nine more workflows, uv caches save from main only, and the labeler tags terraform, talos and observability changes.

## v2.52.1 - 2026-11-08

### Fixed

* KopiurBackupStale no longer fires on an app's standby site, where its backup schedule is suspended on purpose and the last success (from before a switchover) ages past 48h

## v2.52.0 - 2026-11-08

### Changed

* CI: release commits no longer re-run Kubernetes, secret-scan, privacy and the release workflow, and Renovate review only wakes for Kubernetes runs on Renovate branches. About 3,700 of last month's 12,800 runs had nothing new to check.

## v2.51.3 - 2026-11-08

### Fixed

* CI: a hung flate render no longer fails the required Build Success check. Each render attempt is capped at 2 minutes and retried once, with the process state and flate's debug tail printed first; the flate download retries transient errors.

## v2.51.2 - 2026-11-08

### Fixed

* Grafana's Hosts tile grades memory on RAM in use instead of commit charge, which sat near its limit because the WSL VM commits its whole memory and the page file grows on demand; the 95% WindowsHostCommitHigh warning is removed, the 98% critical stays

## v2.51.1 - 2026-11-08

### Fixed

* Grafana host states are reduced across observing Prometheuses like the other states, so a second observer can't duplicate Host rows; docs corrected on which site scrapes the Windows hosts

## v2.51.0 - 2026-11-08

### Changed

* Grafana navigation: a click on data goes one level down in one click and names where (Open Internet, Open Host); runbooks moved to each panel's header; every board's top bar links up to Homelab and its domain board and down to its diagnose boards; Needs attention rows open their own area's board; Homelab gains a Hosts tile from a new per-host state (CPU, memory commit, disk latency, disk space)

## v2.50.0 - 2026-11-08

### Added

* Grafana diagnose boards: Host (CPU, memory, disk latency and network per Windows host, with the WSL VM's pressure), Wi-Fi clients (weak signal, retries and satisfaction per device) and WAN clients (who is filling the Internet line, beside latency), linked from the domain boards

## v2.49.0 - 2026-11-08

### Added

* home-assistant: House systems gains Devices, Backups, Storage and Away access, each recorded per site from its own share and combined in Home Assistant like the other six

## v2.48.0 - 2026-11-08

### Changed

* grafana: state tiles are solid colour instead of a gradient; every board passes an axe colour-contrast check in both themes

### Fixed

* Run changedetection backups and switchover movers as the app user so private watch and history files remain readable.

## v2.47.0 - 2026-11-08

### Added

* observability: screenshot.py renders every board from a checkout against live data in a throwaway Grafana (read-only, output kept out of the repo); CI keeps the browser rehearsal's fixture screenshots as an artifact; a test shows a third site is one CLUSTERS entry and one colour

## v2.46.0 - 2026-11-08

### Changed

* grafana: failure counters that should be zero (WAL, mesh sync, Wi-Fi collector, reconcile, rule evaluation, notifications) are per-site counts over the selected range at the top of their tab, quiet at zero and red at the first failure, instead of empty graphs

## v2.45.1 - 2026-11-08

### Fixed

* grafana: a table row without one of the columns shows a dash there, not the panel's empty-state words (a Flux row under Needs attention read "Nothing needs attention" as its app)

## v2.45.0 - 2026-11-08

### Changed

* grafana: Apps, Hosts and Observability use fleet tables (probe and name state, recovery readiness, required replicas, collectors, the SLO tables); fleet rows are sorted by name so tables of the same things line up

## v2.44.1 - 2026-11-08

### Fixed

* Restore the built-in Energy dashboard and distribution card, removing the custom flow view while retaining the measured Home circle and kWh total.

## v2.44.0 - 2026-11-08

### Changed

* grafana: Network and Data use fleet tables (path by hop, shared VIPs, CNPG role, backups, recovery point and restore time, one row each with a column per site); graph legends name the database; a cell with nothing at that site is a plain dash

## v2.43.0 - 2026-11-08

### Changed

* grafana: Sites & Failover shows each app once with a column per site (workload state, readiness, leases, fencing, restores); status and detail tables sit two across so no column is cut off

## v2.42.0 - 2026-11-08

### Changed

* grafana: the House kiosk is a three-across wall of state words with household wording in each tooltip, no time picker, and the About text folded away

## v2.41.0 - 2026-11-08

### Changed

* Add an Energy distribution view with readable house-to-appliance branches, live power, daily recorded energy and separate solar production.

## v2.40.0 - 2026-11-08

### Changed

* grafana: the Homelab board leads with a domain wall (one rolled-up state per area, each opening its board), a Needs attention list of everything not healthy by area, and folds the coverage evidence into a collapsed row

## v2.39.0 - 2026-11-08

### Changed

* grafana: operator tabs are laid out answer first (headline, status, trends, detail) with About folded away; site colours on per-site graphs, neutral KPI numbers, zero-based axes

## v2.38.6 - 2026-11-08

### Fixed

* Update Home Assistant to 2026.11.0 for measured whole-house consumption on the Energy dashboard and document the Sense, solar and EV meter setup.
* grafana: a state tile with no answer is grey, not green; tables show words and named columns, with units and links on the value column only; graphs show their legends

## v2.38.5 - 2026-11-07

### Fixed

* observability: recovery readiness and restore time show Not applicable, not Unknown, on the site that runs the app

## v2.38.4 - 2026-11-07

### Fixed

* homelab-wsl-02 gets AlertmanagerCrashlooping too. Its addon alerts are listed one by one, so it had lost the chart's rule without gaining the replacement.

## v2.38.3 - 2026-11-07

### Fixed

* The Alertmanager crashloop alert counts restarts from kube-state-metrics instead of process start-time changes. WSL-02's drifting clock made every process there look like it restarted once a minute, so the chart's rule fired a false critical on homelab-wsl-02.

## v2.38.2 - 2026-11-07

### Fixed

* The Network board's mesh hop also needs the site's own probe of its peer to pass. In the partition drill, site 2's clustermesh control plane reached site 1 around the downed tunnel and read ready while pod traffic between the sites failed.

## v2.38.1 - 2026-11-07

### Security

* Fix onboarding callback XSS, restrict collector listeners and workflow permissions, require modern TLS, and prevent sensitive configuration logging and incomplete URL matching.

## v2.38.0 - 2026-11-07

### Added

* CNPG clusters can be rolled from git. Bumping CNPG_ROLLOUT in cluster-settings sets each Cluster's restartedAt annotation, as kubectl cnpg restart does. Its first value rolls every instance once, which gives the running barman-cloud sidecars their 512Mi limit.

## v2.37.3 - 2026-11-07

### Fixed

* StandbyRestoreDrillStale also fires when the drill has never succeeded 8 days after its CronJob was created. Before, it had no series to fire on until a first success.

## v2.37.2 - 2026-11-07

### Fixed

* CNPG's barman-cloud sidecar gets a 512Mi memory limit (was 256Mi). Base backups on homelab-wsl-02 OOMKilled it mid-upload, and the Backup failed with an rpc EOF.

## v2.37.1 - 2026-11-07

### Fixed

* Queue incompatible Coral and UniFi dependency upgrades for review, preserve security alerts, and watch for future compatibility changes.

## v2.37.0 - 2026-11-07

### Added

* The weekly standby restore covers Home Assistant too. It reads StatefulSets as well as Deployments, and creates a StatefulSet's PVC on the standby when it isn't there yet, as site-failover does. All four warm apps with PVCs now get restore evidence and an RTO.

## v2.36.2 - 2026-11-07

### Fixed

* Firefly's failover and the weekly standby restore no longer copy finance-sync-state. Its state has been in Postgres since 2026-11-02, and the PVC is a rollback copy that exists only on homelab-wsl, so site 2's restore failed and a Firefly switchover to it would have been refused.

## v2.36.1 - 2026-11-07

### Fixed

* The weekly standby restore can read site-overrides again. The kustomization's namespace had moved its flux-system Role into kopiur-system, so every run failed with a 403 before restoring anything.

## v2.36.0 - 2026-11-07

### Added

* Pyrra's burn-rate alerts are on for the four SLOs (Internet, Home Assistant's path, shared-VIP names, MQTT). Their longest window is 2 days, so they don't wait for a full 14-day window.

## v2.35.3 - 2026-11-07

### Fixed

* Update observability code and generated collector configuration for Ruff 0.17 lint rules.

## v2.35.2 - 2026-11-07

### Fixed

* Shared VIP routes put a VIP on a site only while that site sends the router prefixes, not just while its BGP session is up (the site-loss drill left 10.10.95.200 on a down site). Both drills are recorded.

## v2.35.1 - 2026-11-07

### Fixed

* The household view reads Unknown, not Failed, for Wi-Fi or cameras when their exporter stops reporting (UniFi Poller on a lost site), as the site-loss drill showed.

## v2.35.0 - 2026-11-07

### Added

* A House systems section on Home Assistant's Home dashboard shows the household six plain states (Internet, Wi-Fi, Automation, Cameras, Apps, Monitoring) from new per-site homelab:house_state rules, and the Monitoring tab (the operator Grafana) is now admin-only.

## v2.34.0 - 2026-11-07

### Added

* Six Grafana preview features on trial (state kept across drilldowns, fitted table columns, auto-height panels, tabular digits, colorblind-safe palettes, recently viewed boards), with the browser rehearsal running the same set.

## v2.33.0 - 2026-11-07

### Changed

* Per-app recovery objectives are final, no longer provisional (RPO 30 h, RTO 1 h for every app, overridable in inventory.py).

## v2.32.1 - 2026-11-07

### Fixed

* `site-failover` no longer lets its SSH calls drain stdin, which made the switchover prompt read EOF when the answer was piped in, and an unreachable site now fails loudly instead of reading as an app that isn't deployed.

## v2.32.0 - 2026-11-07

### Changed

* Grafana reference boards are kept only under a license their publisher states. NGINX uses ingress-nginx's own board, and generated Router and Analytics tabs replace the MikroTik and Plausible boards. Timeseries legends now name each series by the labels its query keeps.

## v2.31.0 - 2026-11-07

### Added

* 28-day availability for each SLO over the metrics archive, with the share of the window each datasource covers, on the Observability board's Objectives tab.

## v2.30.0 - 2026-11-07

### Added

* The Wi-Fi & LAN board measures site 2's Wi-Fi dependency, with each site host's uplink rate and traffic and the site 2 host's signal, access point and association uptime.

## v2.29.0 - 2026-11-07

### Added

* Databases outside CloudNativePG (MongoDB, Immich's Postgres, ClickHouse, Valkey) on the Databases & Backups board, from their workloads' readiness and restarts, and Plausible's probe now requires ClickHouse to answer.

## v2.28.0 - 2026-11-07

### Changed

* State values on the observability boards use colored cells and backgrounds for readable contrast, Unready workloads sums per app instead of one line per collector restart, and the first review of performance, accessibility and privacy is recorded.

## v2.27.0 - 2026-11-07

### Added

* Per-app recovery point and restore time objectives on the Databases & Backups board, from each app's scheduled PVC backups, primary databases and standby restore drills.

## v2.26.0 - 2026-11-07

### Added

* Keep failover, Flux, upgrade and network events as Prometheus series on each site, listed on History tabs of the Kubernetes & GitOps, Sites & Failover and Network & Routing boards.

## v2.25.0 - 2026-11-07

### Changed

* Record the licenses of the pinned reference boards where their publishers state one (UniFi Poller boards are MIT), and mark the rest as pending.

## v2.24.0 - 2026-11-07

### Added

* Document how to reach, recover, review and drill the observability stack on each site.

### Removed

* Retire the Homelab (classic) board, the last one pinned to a single Windows host; its panels are on the generated boards and the Windows Exporter board.

## v2.23.0 - 2026-11-07

### Added

* Rule fixtures for an idle database, an app with no traffic, an expiring certificate and a failed restore drill.

## v2.22.0 - 2026-11-07

### Changed

* The kept hand-made Grafana boards are mounted by their own providers instead of the sidecar, so each UID has one owner and removing a board's file retires it.

## v2.21.0 - 2026-11-07

### Changed

* Telegram alerts from the charts' own rules link to the Alerts board for the alerting site at the incident time, and Alertmanager now knows its external URL.

## v2.20.0 - 2026-11-07

### Changed

* Every alert links to its domain board scoped to the alerting site, so Telegram messages open the right board at the incident time; a test keeps new alerts from skipping it.

## v2.19.0 - 2026-11-07

### Changed

* Replace the empty UI-made Alertmanager / Overview board with the pinned kube-prometheus mixin board under the same UID.

## v2.18.0 - 2026-11-07

### Added

* Operator boards link from a domain panel to its entity board (ingress, router, node, Windows host, access point) on the clicked series or row, keeping the incident time range.

## v2.17.0 - 2026-11-07

### Added

* Deploy Pyrra on both sites with probe-based SLOs (Internet, Home Assistant's name, shared-VIP names, MQTT) over 14-day windows that fit local retention, and show their availability, error budget and window coverage on the Observability board.

### Changed

* Telegram alerts link to their dashboard scoped to the incident, from an hour before the alert started to now while it fires, or to 15 minutes after it resolved.

## v2.16.0 - 2026-11-07

### Added

* Report the metrics archive's lag, history depth, size and free space per site from the Compactor's block index, and alert when it falls over 4 hours behind or runs low on space.
* Probe every shared-VIP name from both sites the way a client does (router DNS, verified TLS, ingress routing), record the certificate expiry and the serving site from a new X-Homelab-Site header, and show which site the router sends each shared VIP to.
* Scrape the Cilium mesh API and KVStoreMesh on both sites, add a mesh hop to the network path state, and report OCIRepository and HelmRepository readiness alongside the other Flux resources.
* The site-lease fence attests each warm app it checked (lease, result, time), and a standby's fence prerequisite is met only by a fresh attestation from the other site, read over the mesh.
* Give Grafana's Alertmanager datasource a fixed UID, add an Alertmanager tab to the Alerts board (alerts held, silences, configuration reload, notifications), and remove the Angular panel plugins Grafana 13 can't load from each site's volume.

## v2.15.0 - 2026-11-07

### Added

* Map every generated dashboard panel to evidence sources, freshness and runbooks, add read-only source auditing, and preserve incident time and multiple sites in diagnosis links.

## v2.14.1 - 2026-11-07

### Fixed

* Keep recovery unknown without per-app fencing and restore proof, reject future evidence, and count selected sites and workload observations once.

## v2.14.0 - 2026-11-07

### Added

* MQTT evidence: each site logs in to mosquitto as a new observability user and reads the connected-client count, for the Home Automation board.

## v2.13.1 - 2026-11-07

### Fixed

* Run Reloader on homelab-wsl-02 too: workloads there kept running old config after a ConfigMap or Secret change (the evidence collector ran 2-hour-old code).

## v2.13.0 - 2026-11-07

### Added

* Recovery evidence per warm app and site (database, backup, restore drill, site lease), so recovery readiness is no longer always Unknown.

## v2.12.0 - 2026-11-07

### Added

* Service state from content probes: each app's health URL, checked for an expected answer from the site that runs it; Standby on the other site.

## v2.11.0 - 2026-11-07

### Added

* Network board: per-site path state by hop (WireGuard tunnel, router BGP, Cilium node BGP) and routes received per site, with router BGP sessions from mktxp.

## v2.10.0 - 2026-11-07

### Changed

* Grafana is a warm app like Firefly: one name (grafana) on the homelab-wsl-first VIP, a standby on homelab-wsl-02 with a replica of its database, moved with task failover. Replaces the separate grafana-02 instance.

## v2.9.1 - 2026-11-07

### Fixed

* Point grafana-02 at site 2 directly, since requests to the shared VIP reached site 1 and got a 404.

## v2.9.0 - 2026-11-07

### Added

* Run a second Grafana on site 2 at grafana-02, with its own database and login, so the dashboards survive losing site 1.

## v2.8.0 - 2026-11-07

### Changed

* Keep 15 days of site 1 metrics on the new C volume instead of 2 days on an emptyDir.

## v2.7.1 - 2026-11-07

### Fixed

* Make the site 1 monitoring volume initialize on Windows PowerShell 5.1, fail the task on errors, and refuse claims before the disk is mounted.

## v2.7.0 - 2026-11-07

### Added

* Add a 20Gi ext4 monitoring volume on C for site 1, mounted into the running worker with no node restart.

## v2.6.0 - 2026-11-07

### Added

* Collect Cilium metrics on site 1, and point the Windows commit alerts at desktop apps rather than restarting WSL.

## v2.5.1 - 2026-11-07

### Fixed

* Probe the archive and Internet paths and report workload state, so the overview shows real status instead of permanent Unknown.

## v2.5.0 - 2026-11-07

### Added

* Scrape the Flux controllers on both sites and turn on Cilium and Hubble metrics on homelab-wsl-02.

## v2.4.0 - 2026-11-07

### Added

* Serve the generated dashboards in Grafana and make Homelab the home page, keeping the previous board as Homelab (classic).

## v2.3.0 - 2026-11-07

### Added

* Label every series with its site and add a partial-tolerant Thanos global view, with block upload to the WSL-02 archive and live evidence rules on both sites.

## v2.2.1 - 2026-11-07

### Fixed

* Correct the Grafana backup health expression and compile all Flux health gates with lifecycle fixtures before deployment.

## v2.2.0 - 2026-11-07

### Changed

* Upgrade Grafana to 13.3 with an authenticated bounded renderer, preserve existing dashboard and datasource identities, and provision pinned reference exports from git.

## v2.1.2 - 2026-11-07

### Fixed

* Run the single-node archive with a Recreate Deployment and retained claims so failed revisions can recover declaratively.

## v2.1.1 - 2026-11-07

### Fixed

* Copy Garage credentials into an owner-only runtime volume so its strict permission check works with Kubernetes fsGroup.

## v2.1.0 - 2026-11-07

### Added

* Deploy a private Garage object store on WSL-02 and read-only evidence collectors on both sites, prepare the Grafana database upgrade backup, and add generated dashboards and bounded Thanos components with offline failure tests.

## v2.0.4 - 2026-11-07

### Fixed

* wsl_time creates /etc/systemd/timesyncd.conf.d first: Ubuntu ships none, so the apply after #1874 failed on both distros.

## v2.0.3 - 2026-11-07

### Fixed

* Both WSL sites keep time the same way: Windows Time (windows_time) and the distro's timesyncd (wsl_time) sync from the router. WSL-02 goes back to the TSC page clocksource: the MSR one ran just as slow (its slow VM clock is a host problem, the BIOS first).

## v2.0.2 - 2026-11-07

### Fixed

* WSL-02's VM clock ran ~13% slow on the Hyper-V TSC page clocksource (every timer in homelab-wsl-02 slow, AlertmanagerClusterCrashlooping firing); its .wslconfig now pins hyperv_clocksource_msr, a per-host wsl_clocksource in windows_host_settings.

## v2.0.1 - 2026-11-07

### Fixed

* The site distro masquerades node egress by any interface but the site tunnel (as Docker did), so the desktop cluster reaches SW03 and the APs through wg01-home again (SNMP, smokeping); PGReplication skips replica clusters, whose idle-time lag isn't lag (StandbyReplicaBehind covers them).

## v2.0.0 - 2026-11-07

### Breaking

* Two WSL sites, one platform. homelab-wsl and homelab-wsl-02 both run Cilium (homelab-wsl moved in place from Flannel) with no kube-proxy and no MetalLB: LB IPAM with L2 announcements, BGP to the router over per-site WireGuard, and Cluster Mesh with global Services. Shared VIPs (internal ingress, MQTT) fail over between sites. Every app has its own CNPG cluster and the shared databases/postgres cluster is retired; movable apps keep a warm standby on the other site, and bin/site-failover moves them (UniFi, Ombi, Wizarr, Jellyfin, Plausible and the transcriber now run on homelab-wsl-02).

## v1.226.0 - 2026-11-07

### Changed

* Plausible runs on homelab-wsl-02 with its Postgres primary there; homelab-wsl keeps a standby (database replica, app at 0), and analytics.tnwks.us reaches it through cloudflared over Cluster Mesh.

## v1.225.0 - 2026-11-07

### Removed

* The shared databases/postgres CNPG cluster is retired on homelab-wsl: every app runs on its own cluster. Its last backup stays in S3; the CNPG alerts now wait for the operator instead, and the apps' finished import patches are gone.

## v1.224.0 - 2026-11-07

### Added

* Plausible can move between the WSL sites: its Postgres runs as a CNPG replica cluster on homelab-wsl-02 (cnpg-standby), its Service is global with service-upstream Ingresses, and bin/site-failover moves its ClickHouse volume with a transfer-only kopiur policy.

## v1.223.0 - 2026-11-07

### Changed

* Plausible gets its own CNPG cluster (databases/plausible-pg), imported from the shared cluster, the last app to leave it; its owner-role Secret moves next to it.

## v1.222.0 - 2026-11-07

### Changed

* Jellyfin runs on homelab-wsl-02 (with an empty library; the media on the desktop's E: stays put): homelab-wsl keeps it at 0, jellyfin.tnwks.us reaches it through cloudflared over Cluster Mesh, and jellyfin.internal.tnwks.us is on the homelab-wsl-02-first shared VIP.

## v1.221.0 - 2026-11-07

### Added

* Jellyfin can move between the WSL sites: replica toggle, global Service and Service-upstream Ingresses, a transfer-only kopiur policy for its config, a site-failover entry (prebind with its library volume, root restore), and homelab-wsl-02 deploying it at 0. Its media library stays on the desktop.

## v1.220.1 - 2026-11-07

### Fixed

* site-failover clear and status only touch an app's own override keys when prefixes nest: clearing ombi no longer clears ombi-f's OMBI_F_REPLICAS.

## v1.220.0 - 2026-11-07

### Changed

* Ombi, Ombi-f and Wizarr run on homelab-wsl-02; homelab-wsl keeps them at 0, and their public names reach them through cloudflared over Cluster Mesh. site-failover's app check passes on exit 0 even when the command prints nothing.

## v1.219.1 - 2026-11-07

### Fixed

* Ombi and Ombi-f can move: homelab-wsl-02 has the SECRET_PREFIX cluster secret Ombi-f's HelmRelease needs, and their restores run as root so they can write into the chart-created PVC. site-failover gains --no-rollback (fix forward: a failed copy leaves the app stopped instead of moving it back) and refuses before stopping anything when a target PVC is missing.

## v1.219.0 - 2026-11-06

### Added

* Ombi, Ombi-f and Wizarr can move between the WSL sites: replica toggles, global Services and Service-upstream public Ingresses (cloudflared reaches them over Cluster Mesh), transfer-only kopiur policies, site-failover entries, and homelab-wsl-02 deploying them at 0. The production namespace allows root kopiur movers (Wizarr's root-owned database).

## v1.218.0 - 2026-11-06

### Added

* MQTT on a shared VIP: mqtt.internal.tnwks.us (10.10.95.11, homelab-wsl first) reaches the broker on whichever site runs it. homelab-wsl-02 deploys mosquitto at 0 as a standby, and the broker's Services are global; the per-site address behind the desktop's LAN bridge stays.

## v1.217.0 - 2026-11-06

### Added

* Public names follow their apps between the WSL sites: Mealie's Service is global, and Home Assistant's and Mealie's public Ingresses go through the Service, so cloudflared and the public ingress on homelab-wsl reach the app over Cluster Mesh when it runs on homelab-wsl-02.

## v1.216.1 - 2026-11-06

### Fixed

* Internal names resolve for musl (Alpine) clients too: a public wildcard TXT for *.internal.tnwks.us turns the AAAA answer the router forwards from NXDOMAIN into NODATA, so the router's A record is used.

## v1.216.0 - 2026-11-06

### Changed

* Internal names on the shared VIPs: the apps deployed on both WSL sites resolve to 10.10.95.200 (homelab-wsl-02 first) or 10.10.95.10 (homelab-wsl first: Home Assistant, Firefly), so a site failure or a switchover moves their traffic with no DNS change. The mobile VPN may reach the shared VIPs.

## v1.215.0 - 2026-11-06

### Changed

* Two shared VIPs instead of one, each preferring the site that runs its apps: the router's VIP pool is split in halves (10.10.95.0/25 homelab-wsl first, 10.10.95.128/25 homelab-wsl-02 first), and the internal ingress is on 10.10.95.10 and 10.10.95.200, so an app's traffic doesn't cross the tunnels twice.

## v1.214.1 - 2026-11-06

### Fixed

* Firefly's internal login check uses the site's oauth2-proxy URL, so homelab-wsl-02's ingress (the shared VIP, or a switchover) serves it instead of failing with 500.

## v1.214.0 - 2026-11-06

### Added

* Home Assistant and Firefly have global Services too, and their and UniFi's internal Ingresses go through the Service, so either site's ingress serves every app that is deployed on both sites, wherever it runs.

## v1.213.0 - 2026-11-06

### Added

* The apps that run on homelab-wsl-02 (transcriber, changedetection, Immich, immichframe, the kitchen screensaver, qBittorrent, playwright-mcp) have global Services, and their internal Ingresses go through the Service, so either site's ingress, and so the shared VIP, serves them over Cluster Mesh.

## v1.212.0 - 2026-11-06

### Added

* The first shared VIP: both WSL clusters' internal ingress on 10.10.95.200, announced over BGP, with the router preferring homelab-wsl and failing over to homelab-wsl-02. DNS doesn't use it yet.

## v1.211.1 - 2026-11-06

### Fixed

* nfs-server reconciles again: the gdrive-sync PVC stays on local-path (ext4 on worker-1), as it was created, instead of a class patch Flux can't apply to a bound claim.

## v1.211.0 - 2026-11-06

### Added

* A least-privilege path into Google Drive: nfs-server exports /gdrive-sync (cluster nodes write, squashed to one uid; the desktop only reads), UniFi's .unf lands there from whichever site runs the controller, and the desktop's tnwks-gdrive-sync copies new files into G:\My Drive\Backup\nfs-gdrive-sync, mounting only that folder and only inside its own unit. It replaces tnwks-unifi-backup.

## v1.210.1 - 2026-11-06

### Fixed

* Ansible Hosts: the controller cache (Coral shim and Windows downloads) is kept in the Actions cache, so most runs skip installing mingw-w64 from GitHub's Ubuntu mirror, whose stalled downloads failed plan jobs; a cache miss retries against archive.ubuntu.com.
* UniFi on homelab-wsl-02: the UI's login check uses the site's oauth2-proxy URL instead of an in-cluster address that only exists on homelab-wsl, and the inform Service is global, so APs still informing the desktop (a cached `unifi` address) reach the controller over Cluster Mesh.

## v1.210.0 - 2026-11-06

### Changed

* The UniFi controller runs on homelab-wsl-02: the router's `unifi` record (the APs' inform host) and `unifi.internal.tnwks.us` point at WSL-02, and homelab-wsl keeps the controller and unpoller at 0. Home Assistant reaches it over Cluster Mesh.

## v1.209.1 - 2026-11-06

### Fixed

* UniFi's MongoDB volume can be copied between sites: the probes' mongosh logs no longer land in the database volume (HOME=/tmp), and the transfer snapshot skips the old ones, which the mover couldn't read.

## v1.209.0 - 2026-11-06

### Added

* The UniFi controller's Service is global across the WSL sites (Cilium Cluster Mesh), so Home Assistant, unpoller and CI reach it on either site; the desktop's .unf copy to Google Drive skips cleanly while the controller runs on the other site.

## v1.208.1 - 2026-11-06

### Fixed

* homelab-wsl-02's internal sites answer again: Cilium's L2 announcements ignore externalTrafficPolicy, and after the agents restarted, the ingress VIP's lease went to a node without an ingress pod, which dropped the traffic. Ingress and k8s-gateway use externalTrafficPolicy Cluster on both WSL clusters.

## v1.208.0 - 2026-11-06

### Added

* Cilium Cluster Mesh between homelab-wsl and homelab-wsl-02: a shared Cilium CA, a clustermesh-apiserver (KVStoreMesh) on a LoadBalancer VIP at each site (10.5.0.210, 10.6.0.210), and native routing for both sites' pods over the routed network, so Services can be global across the sites.

## v1.207.0 - 2026-11-06

### Removed

* MetalLB is gone from homelab-wsl: Cilium LB IPAM and L2 announcements hand out and answer for its LoadBalancer IPs (the same 10.5.0.200-220 range and addresses), as on homelab-wsl-02. Shared VIPs no longer need a loadBalancerClass there.

## v1.206.3 - 2026-11-06

### Fixed

* homelab-wsl's API server is quiet again: Cilium's LB IPAM, woken by the shared VIP pool, kept clearing the status of MetalLB's LoadBalancer Services while MetalLB rewrote it (about 480 API requests a second). On homelab-wsl Cilium now takes only Services that ask for its class.

## v1.206.2 - 2026-11-06

### Fixed

* The sites pages and the sites-builder runner mount their NFS share again on homelab-wsl without kube-proxy: nfs-server runs on its node's host network and the mounts use that node's address, because the kernel NFS client can't reach a ClusterIP through Cilium's kube-proxy replacement. The export now admits only the node subnet.

## v1.206.1 - 2026-11-06

### Fixed

* homelab-wsl runs without kube-proxy, now that Cilium's kube-proxy replacement handles Services there.

## v1.206.0 - 2026-11-06

### Added

* Cilium BGP on homelab-wsl too: its nodes peer with the distro's FRR and announce their pod CIDRs, and the router and homelab-wsl-02's distro accept homelab-wsl's new pod range (10.46.0.0/16) instead of Flannel's 10.42.

## v1.205.1 - 2026-11-06

### Fixed

* The Weave GitOps dashboard is reachable again on homelab-wsl: Flux's NetworkPolicies, now enforced by Cilium, only admit traffic from inside flux-system, so a policy lets the ingress controllers (and Prometheus, for metrics) in.

## v1.205.0 - 2026-11-06

### Changed

* homelab-wsl runs on Cilium: Flannel is removed, its pod network is 10.46.0.0/16 (cluster-pool IPAM, kept for rebuilds through CILIUM_IPAM_MODE), and it uses the shared Cilium Kustomization like homelab-wsl-02. NetworkPolicies are enforced on every cluster now.

## v1.204.0 - 2026-11-06

### Changed

* homelab-wsl runs entirely on Cilium's datapath: kube-proxy replacement, BPF masquerade and host routing, and NetworkPolicy enforcement, now that every pod has a Cilium address. The migration node config is removed.

## v1.203.0 - 2026-11-06

### Changed

* homelab-wsl's Cilium move: a CiliumNodeConfig lists the nodes Cilium takes over, each from its agent's next restart.

## v1.202.0 - 2026-11-06

### Changed

* homelab-wsl starts its move from Flannel to Cilium: Cilium runs next to Flannel in migration mode with its own pod range (10.46.0.0/16), and ingress-nginx trusts both pod ranges meanwhile. No pod moves until a node is enrolled.

## v1.201.0 - 2026-11-06

### Changed

* Home Assistant's trusted proxies are in git: the init container adds every WSL site's pod CIDR (10.42, 10.44, 10.46) to HA's HTTP settings store before it starts, so a switchover to homelab-wsl-02, or homelab-wsl's move to Cilium, keeps the ingress trusted with no UI step.

## v1.200.2 - 2026-11-06

### Fixed

* immich-sync runs again on homelab-wsl-02: it failed every hour with "Name does not resolve" for Home Assistant's LAN name, because the Alpine image's musl resolver gives up on the router's NXDOMAIN answer for AAAA. It now uses the glibc (slim) Python image.

## v1.200.1 - 2026-11-06

### Security

* privacy-check fails on AI attribution (a tool's "generated with" line, its link, or a co-author trailer naming the vendor) in commit messages, PR titles and bodies, and comments, so the hooks and the required privacy check stop it before it is published.

## v1.200.0 - 2026-11-06

### Added

* Stage B of the routed multi-site network, ready to run: `talos_clusters` carries each WSL cluster's Docker network options (nat-unprotected, no masquerading, bridge `br-site1`/`br-site2`), `01-talos-bootstrap.yml` creates the network with them when it's missing and only warns when an existing one differs, and `talos/wsl/recreate-network.sh` (`task talos:network:wsl`, `talos:network:wsl-02`) moves a running cluster onto it in place, about 90 s down, with `--dry-run` and `--rollback`. Both distros turn on `wsl_site_routing_masquerade` now (no change next to Docker's own masquerade). The swap itself is the owner's step.

## v1.199.2 - 2026-11-06

### Fixed

* Kitchen display: the to-do lists (routines, rewards, lists, notes) subscribe again by themselves after an HA restart, so the wall display no longer keeps the day before's checks when the first try after the restart is rejected; the AM/PM week grid shows the whole week (the history request had no end_time, so HA returned only Sunday).

## v1.199.1 - 2026-11-06

### Fixed

* homelab-wsl-02 runs without kube-proxy: Talos kept deploying it next to Cilium's kube-proxy replacement, because deleting the KubeProxyConfig document doesn't disable it; the patch now also sets cluster.proxy.disabled.

## v1.199.0 - 2026-11-06

### Added

* Cilium BGP on homelab-wsl-02: every node peers with its distro's FRR and announces its pod CIDR (and, later, shared VIPs from a new `shared-vip` pool), so the router learns the site's pods. The control plane is off by default (`CILIUM_BGP_ENABLED`); homelab-ms-01 is unchanged.

## v1.198.0 - 2026-11-06

### Added

* Site routing, Stage A: each WSL distro gets its WireGuard site tunnel to the router (wg-quick), FRR 10.5 in a pinned container peering BGP over it (with a Cilium peer group ready for Stage C), and persistent forwarding, MSS clamping and connmark return routing (role wsl_site_routing, tag site-routing).

## v1.197.1 - 2026-11-06

### Fixed

* Ansible Hosts: the controller-tools apt step is time-boxed and retried, so a stalled download on GitHub's Ubuntu mirror no longer runs the plan job into its 20-minute limit.

## v1.197.0 - 2026-11-06

### Added

* The UniFi controller can move between the WSL sites: homelab-wsl-02 deploys it (and unpoller) at 0 with its own inform VIP and LAN bridge, the router admits the APs to either host, `bin/site-failover` copies both of its volumes, and UniFi metrics are federated to the other site.

## v1.196.1 - 2026-11-06

### Fixed

* Pod networks of every WSL site: qBittorrent's auth whitelist covers homelab-wsl-02's pods (and homelab-wsl's coming Cilium range), the Cilium IPAM mode is a cluster setting so a cluster moved from Flannel rebuilds the way it runs, and the Home Assistant docs list every site's pod CIDR for its trusted proxies.

## v1.196.0 - 2026-11-06

### Added

* Each WSL site watches the other: Prometheus federates from the other site over the LAN bridge (port 9090, peer only) and alerts when that site is unreachable or a standby replica falls behind its primary.

## v1.195.0 - 2026-11-06

### Changed

* The media stack runs on homelab-wsl-02 (moved with task failover) until a target-state decision; qbittorrent's internal name resolves to WSL-02.

## v1.194.3 - 2026-11-06

### Fixed

* The media stack's switchover restores with kopiur's privileged mode (a plain uid-0 mover has no capabilities and can't read other users' private files).

## v1.194.2 - 2026-11-06

### Fixed

* Home Assistant's standby builds on homelab-wsl-02 (its Prometheus token secret is encrypted for that site), and the media stack's switchover restores as root so owners survive.

## v1.194.1 - 2026-11-06

### Fixed

* The router's BGP connections to the WSL sites are created with the restapi provider, since the routeros provider can't write them on RouterOS 7.22+ (it always sends the removed add-path-out).

## v1.194.0 - 2026-11-06

### Added

* Home Assistant has a warm standby on homelab-wsl-02 (its recorder database a replica, the app at 0, backups suspended there), fenced by the home-assistant site lease.

## v1.193.0 - 2026-11-06

### Added

* mikrotik: WireGuard site tunnels (wg-site1, wg-site2), their firewall zones and eBGP with the WSL sites' distro FRR, for the multi-site routed network (docs/wsl/multi-site.md)
* A site lease for Home Assistant on the router, ahead of its standby on homelab-wsl-02.

## v1.192.1 - 2026-11-06

### Fixed

* task failover can prebind an app's PVCs on the new site (start it once, then stop) so apps whose pods share a local-path PVC don't end up split across nodes; the media stack uses it.

## v1.192.0 - 2026-11-06

### Added

* The media stack (sonarr, radarr, prowlarr, qbittorrent, recyclarr) is deployed on homelab-wsl-02 at 0, ready to move there with task failover (configs copied by kopiur, transfer only).

## v1.191.1 - 2026-11-06

### Fixed

* UniFi's Terraform workflows run only on homelab-wsl's ops runner (tnwks-ops-homelab-wsl), where the controller API is; each site's runner carries a site label.

## v1.191.0 - 2026-11-06

### Added

* A second network-device Terraform runner on homelab-wsl-02 (tnwks-ops-runner-wsl-02, label tnwks-ops-runner) with its own pod-contained wg03 WireGuard peer, so router and EdgeOS workflows run while homelab-wsl is down.

## v1.190.1 - 2026-11-06

### Fixed

* task failover:clear applies each site's cluster-settings from git before it drops an app's overrides, so the app can't fall back to defaults on the old site.

## v1.190.0 - 2026-11-06

### Changed

* Immich, immichframe and kitchen-screensaver run on homelab-wsl-02 (moved with task failover); their names resolve to WSL-02.

## v1.189.2 - 2026-11-06

### Fixed

* task failover's final check works for apps checked with a command (Immich), not only a URL.

## v1.189.1 - 2026-11-06

### Fixed

* homelab-wsl-02's cluster-secrets has OAUTH2_PROXY_CLIENT_ID, which Firefly's logout link needs, so Firefly's standby builds there.

## v1.189.0 - 2026-11-06

### Added

* Firefly and finance-sync have a warm standby on homelab-wsl-02 (both databases replicas, the app at 0, jobs and backups suspended there), fenced by the firefly site lease; task failover moves both databases together.

## v1.188.0 - 2026-11-06

### Added

* A site lease for Firefly (and finance-sync) on the router, ahead of its standby on homelab-wsl-02.

## v1.187.1 - 2026-11-06

### Fixed

* task failover rolls an app back onto the old site when the PVC copy fails, and fails fast on a refused kopiur mover; Immich's movers run unprivileged.

## v1.187.0 - 2026-11-06

### Added

* Immich, immichframe and kitchen-screensaver are deployed on homelab-wsl-02 at 0, ready to move there with task failover (library and Postgres copied by kopiur, transfer only).

## v1.186.0 - 2026-11-06

### Changed

* changedetection runs on homelab-wsl-02 (moved with task failover); its name resolves to WSL-02.

## v1.185.0 - 2026-11-06

### Added

* task failover moves apps without a standby database too (StatefulSets, PVCs the restore creates); changedetection is deployed on homelab-wsl-02 at 0, ready to move there.

## v1.184.0 - 2026-11-06

### Changed

* Grafana, LiteLLM, Vaultwarden, packing-api and the things API have their own Postgres clusters, imported from the shared one, which keeps its copies as the rollback.

## v1.183.0 - 2026-11-06

### Changed

* Grafana, LiteLLM, Vaultwarden, packing-api and the things API are stopped while their databases move to their own CNPG clusters.

## v1.182.0 - 2026-11-06

### Added

* Fencing for the warm apps: an app gate (waits while its site lease fences this site), a per-site database fence CronJob, and task failover --promote for an unplanned failover (refuses until the lease fences the dead site).

## v1.181.0 - 2026-11-06

### Added

* Site leases on the router for the warm apps (<app>.lease.tnwks.local): the site fenced from writing an app after an unplanned failover, or none.

## v1.180.0 - 2026-11-06

### Changed

* docs: the fencing design for an unplanned failover between the WSL sites (site leases on the router, an app gate, a database fence).

## v1.179.0 - 2026-11-06

### Added

* The transcriber's clip audio is backed up nightly by kopiur on the site that runs it, and restored weekly onto the standby.

## v1.178.1 - 2026-11-06

### Fixed

* homelab-wsl-02's Talos node containers come back after a reboot: Ansible sets restart policy unless-stopped on every Talos node container (talosctl 1.14 creates them with no).

## v1.178.0 - 2026-11-06

### Changed

* Home Assistant's recorder has its own Postgres cluster (home-assistant-pg), imported from the shared one, which keeps its copy as the rollback.

## v1.177.0 - 2026-11-06

### Changed

* Home Assistant is stopped while its database moves to its own CNPG cluster.

## v1.176.0 - 2026-11-06

### Changed

* litellm: prompt caching is on for every Claude model (`enable_anthropic_prompt_caching`), so requests without their own `cache_control` marks are cached too

## v1.175.0 - 2026-11-06

### Changed

* Firefly and finance-sync have their own Postgres clusters (firefly-pg, finance-sync-pg), imported from the shared one, which keeps its copies as the rollback.

## v1.174.0 - 2026-11-06

### Changed

* Firefly is stopped while its and finance-sync's databases move to their own CNPG clusters.

## v1.173.0 - 2026-11-06

### Changed

* The disaster-recovery doc covers moving an app between the two WSL sites, with the first drills' RTO and RPO.

## v1.172.1 - 2026-11-06

### Fixed

* task failover waits for the app to answer on the new site before it reports done (a Deployment can be Ready before the app listens).

## v1.172.0 - 2026-11-06

### Added

* A weekly standby restore on both WSL sites: the other site's newest kopiur snapshot of each warm app's PVC into this site's copy, alerting when it fails or goes stale.

## v1.171.2 - 2026-11-06

### Fixed

* task failover's preflight checks the replica's WAL replay against the old site's archive (it printed None on a replica).

## v1.171.1 - 2026-11-06

### Fixed

* ingress-nginx-internal resolves names over IPv4 only, so homelab-wsl-02's auth subrequest to homelab-wsl's oauth2-proxy works (the router answers AAAA with NXDOMAIN).

## v1.171.0 - 2026-11-06

### Changed

* The transcriber runs on homelab-wsl-02 (moved with task failover); homelab-wsl keeps its standby.

## v1.170.0 - 2026-11-06

### Changed

* transcriber.internal.tnwks.us resolves to WSL-02: the transcriber runs on homelab-wsl-02.

## v1.169.0 - 2026-11-06

### Added

* task failover (bin/site-failover): planned switchover of an app between homelab-wsl and homelab-wsl-02, with site-overrides, kopiur PVC copy and CNPG demote/promote with the token.

## v1.168.0 - 2026-11-06

### Added

* kopiur works in both directions between the WSL sites: each has the other's repository read-only, and homelab-wsl-02 carries the policies of its standby apps (schedules suspended), plus a transfer-only policy for the transcriber's audio.

## v1.167.0 - 2026-11-06

### Added

* The transcriber has a standby on homelab-wsl-02 (CNPG replica cluster from S3, the app at 0, its upstreams on homelab-wsl over the LAN bridge); homelab-wsl-02 creates apps' ServiceMonitors now that it runs Prometheus.

## v1.166.0 - 2026-11-06

### Changed

* The transcriber has its own Postgres cluster (transcriber-pg), imported from the shared one, which keeps its copy as the rollback.

## v1.165.0 - 2026-11-06

### Changed

* The transcriber is stopped while its database moves to its own CNPG cluster.

## v1.164.0 - 2026-11-06

### Changed

* The transcriber's replicas, upstream endpoints (Frigate RTSP, MQTT, LiteLLM, oauth2-proxy) and Helm wait come from cluster-settings, so another site can run it; TranscriberDown only fires where it's meant to run.

## v1.163.0 - 2026-11-06

### Added

* homelab-wsl exposes Frigate's go2rtc RTSP restream and LiteLLM to WSL-02 only (LAN bridge ports 8554 and 4000), for apps that move to homelab-wsl-02.

## v1.162.0 - 2026-11-06

### Added

* Mealie has a warm standby on homelab-wsl-02: its Postgres is a replica cluster of homelab-wsl's (WAL from S3, kubernetes/components/cnpg-standby), and Mealie is deployed there at 0 replicas.

## v1.161.0 - 2026-11-06

### Changed

* playwright-mcp runs on homelab-wsl-02 only; homelab-wsl's copy is scaled to 0.

## v1.160.3 - 2026-11-06

### Fixed

* Site DNS records under *.internal.tnwks.us (playwright-mcp on WSL-02) now answer instead of the wildcard.

## v1.160.2 - 2026-11-06

### Fixed

* Exact router DNS names (like a site record under *.internal.tnwks.us) now answer ahead of the wildcards that cover them.

## v1.160.1 - 2026-11-06

### Fixed

* Mealie now waits for its Postgres cluster to report Ready before it starts.

## v1.160.0 - 2026-11-06

### Changed

* Mealie has its own Postgres cluster (mealie-pg), imported from the shared one, which keeps its copy as the rollback.

## v1.159.0 - 2026-11-06

### Added

* The MikroTik answers `playwright-mcp.internal.tnwks.us` with WSL-02 (10.10.91.112, TTL 5m), more specific than the `*.internal.tnwks.us` wildcard: step 2 of moving playwright-mcp to homelab-wsl-02. Names served by homelab-wsl-02 live in `environments/prod/mikrotik/dns-sites.tf`.

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
