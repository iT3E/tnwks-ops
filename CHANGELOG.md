# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

## v2.290.0 - 2026-11-22

### Added

* agents: the main agent gets its browser tools: the server entry of the browser route and its 21 tools, each by name in the sandbox allow list

## v2.289.0 - 2026-11-22

### Added

* security: the Wazuh server for step 2 of the security platform (task 8.3, 8.4 and 8.5), written and not listed by any cluster, so Flux applies none of it. An overlay on the vendor manifests of wazuh-kubernetes at v5.0.0-rc2 (images by digest): one manager, one indexer with single-node discovery and no privileged init container, and the dashboard, each with a node selector and the memory line of the security README. The overlay adds certificates from a CA of the server in cert-manager, the manager configuration (HTTPS on 1517 only, no legacy listener or legacy enrollment, no remote upgrade), the egress of the indexer to its content feed, the LoadBalancer of the agents, and the settings of the indexer from git in two steps: a Job that applies the accounts that carry its mark, with each hash from a SOPS Secret, only when its files change, and a Job at each change of its files plus a daily CronJob that apply the ISM retention of tnwks, move each index to it, start no move in the hour before a certificate renewal, and write the count of indices not on a policy of tnwks for the converter. A CI test pins each age, so a merge that lowers one needs an edit of the pin. To prove at the first run on site 2: the indexer reaches its feed through its egress rule only, step 2 needs no ISM action beyond the five of its role, a backing index made by a rollover takes the policy of tnwks, and which audit and alert files the image writes.

## v2.288.0 - 2026-11-22

### Added

* Agent platform: the platform Vector also counts one fixed log line of the approval plugin, when it refuses each tool call (pattern plugin_refuse of homelab_agent_channel_error_lines_total; only the count leaves the node), and AgentPluginRefusingTools warns for an hour after such a line

## v2.287.1 - 2026-11-22

### Fixed

* agents: the straight tries of the gateway probes to the two vault servers pass only on a time out, as the tries to the read servers do; a refusal, an answer or a name that does not resolve fails

## v2.287.0 - 2026-11-22

### Changed

* ansible: the roles of the user of the host server change a host only with -e host_mcp_agent_apply=true; without it, as in each CI run, they check only and a difference fails the run with a drift message, and each role comes after the roles that apply on its host; the comment of the distro account has no colon, which useradd refused

## v2.286.0 - 2026-11-22

### Changed

* windows: site 2 checks its .wslconfig and no longer writes it, the convention of a host with a cluster, as site 1 does

## v2.285.0 - 2026-11-22

### Added

* agents: the encrypted Secret of the host server of main, the values of its user in the host secrets of site 1 with the switch off, and the address of the SSH server of the distro in the settings of site 1; not applied until the owner applies the user and passes his gates

## v2.284.0 - 2026-11-22

### Added

* agents: the manifests of the host server of the main agent at site 1 (ssh-mcp from the image of ours, a proxy in its pod that sets its token, the network rule) and its gateway route with a key of its own and the six read tools; the audit log of the server has a volume of its own, its config a folder of mode 0700, and its lab a fake host that proves a read returns output; not applied until the values of its Secret exist

## v2.283.0 - 2026-11-22

### Added

* Observability: FluxResourceNotReady warns when a Flux object of a site has had Ready False for one hour, on the rules that both sites run, from the evidence collector of each site; site 2 had no alert for a Flux object that is not Ready

## v2.282.0 - 2026-11-22

### Added

* Ansible: the roles windows_host_mcp_user and wsl_host_mcp_user make the local user of the host server of the main agent on the Windows host and in the distro of a site, with no admin group, a key that holds only for the address from which the cluster connects (on Windows, the address of the distro), and a key file and shell startup files that only an admin changes (docs/wsl/host-mcp-user.md names the limits on Windows); windows_sshd gives it a Match block of its own. Off on each site until the owner turns it on, so this change makes no change on a host

## v2.281.1 - 2026-11-22

### Fixed

* security: the runbook of the security platform names the cluster of each kubectl read and the Loki of Trivy Operator on homelab-wsl, makes each read on a Windows host wait for the owner, and says that the compaction of the WSL disk stops the cluster of site 2 for its run

## v2.281.0 - 2026-11-22

### Added

* security: a runbook for the alerts of the security platform (docs/apps/security-platform-runbook.md), one section for each of its 15 alerts, and each of those alerts links to its section (runbook_url)

## v2.280.3 - 2026-11-22

### Fixed

* Flux: the reduced substitution Secret cluster-secrets-apps builds on the cluster of kubernetes/clusters/wsl-02. Flux substitutes in strict mode, and that cluster has a template of its own with only the keys of its cluster-secrets. A test checks each variable of the template of each site against the key names of the cluster-secrets of that site, read with no value

## v2.280.2 - 2026-11-22

### Security

* Flux: the database folders of the apps join the "ask first" class, and databases is a namespace of the class (D149). The rule operator-pods of the admission policy flux-child-scope now covers each object of the database operator and of its backup plugin, so a child outside the class can not apply a database cluster, a pooler, a backup or an object store. The binding stays at Audit and Warn; a replay of the render of git of both sites finds no refusal

## v2.280.1 - 2026-11-22

### Fixed

* playwright-mcp: the browser of main takes the Host that the central MCP gateway sends (the name of its Service with no port), so the route main-browser answers; the route is not live for use yet

## v2.280.0 - 2026-11-22

### Added

* mcp-gateway: the browser route of the main agent (stateful, the core tools of the pinned browser image but those that run code or send a file of the pod), the browser pinned by digest with the flags of the design and its pages through the fetch proxy, the network rules, and a prompt to the approver before each browser tool that can send a form; deployed, not live for use: the agent gets no browser tool until a later switch

## v2.279.2 - 2026-11-22

### Fixed

* ci: the observability checks no longer take 15 minutes. The tests of observability/tests ran one after the other in the contracts job and were 13 of them: they are their own job on two runners, one worker for each CPU and a file on one worker, and the lab of the scheduled jobs runs a sync pass each second, not each 5. Three runners share the PowerShell tests (`--shard I/N` of security/tests), and each test step prints its slowest tests

## v2.279.1 - 2026-11-22

### Security

* Owner label: a pull request of the App of the main agent outside the "ask first" class also waits for owner/approved when its content reaches past the class: a link or a submodule, a file under kubernetes/ that holds a Flux object or an admission object, or a path with ".." that leaves the folder of its file. A file it can not read holds the pull request too
* Owner label: until the Flux children outside the "ask first" class decrypt with a key of their own, a pull request of the App of the main agent that adds, changes or removes an encrypted file (".sops." in its name) waits for owner/approved
* Owner label: the content rule of a pull request of the App of the main agent parses each file under kubernetes/ as YAML, so anchors, tags and folded scalars read as Flux reads them; it also holds encrypted content whatever the name of its file, a remote source of a kustomization, a file that does not parse, and a repeated key. The parser is PyYAML, pinned by hash; with no parser each such file holds the pull request

## v2.279.0 - 2026-11-22

### Added

* agents: the main agent runs the agent image 0.5.0 and loads the host approval plugin, with the approval form of the design (plugin cards to the approver only, the command rule and the deny list), and its values check stops the start on a fault of that form; no host tool yet

## v2.278.1 - 2026-11-22

### Security

* CI: the gates that run code of a branch ask pr-trust.yaml first, which runs owner-label-check.py --trust of main. For anyone but the owner or Renovate alone, the job Changes runs bin/ci-changes and .github/ci of main from the merge base and fails a PR whose base is not main, the rendered diff runs the scripts of main, the Terraform plan waits for the owner, the EdgeOS plan does not run, and the host check waits for the approval of the owner. The job Changes chooses the jobs before any code of the branch runs in it. A PR of Renovate runs nothing in the host workflow, as before. The host workflow and the link check run a hand start on main only, and an apply of the host workflow waits in the environment prod. The privacy check and the secret scan run on each push to a branch under agent/, with no path filter. A new workflow, merge-notice.yaml, posts each merge of the App of the main agent to the owner, and opens an issue for the owner when the notice fails. Test: .github/scripts/test_ci_gates.py.

## v2.278.0 - 2026-11-22

### Added

* homelab-wsl: alerts on the memory of the node containers, which the kubelet does not see (it reports the whole WSL VM): WorkerContainerMemoryNearLimit (a warning at 90% of 13 GiB for 5 minutes, critical at 95% for 2 minutes) and ControlPlaneContainerMemoryNearLimit (98% of 4 GiB for 15 minutes, warning); site 1 now scrapes the working set of its nodes

## v2.277.0 - 2026-11-22

### Changed

* Security: the firmware test of the API checks job counts an age that it does not know as old. A router that runs a version older than each release that the job has seen fails at once, and a release that the job sees first is dated by its last read of the feed, or by the date of the feed if that is earlier, so a stop of the job never moves a fail late. The list of releases keeps the time of the last read (version 2).
* Security: the log of each run of the API checks job says when the version lines of the router and Prometheus agree, so it names the source of the version in each case. The tests of the version line add a tag that only contains the prefix, a version that is not one, and a line that is not the JSON of an event.

## v2.276.0 - 2026-11-22

### Changed

* windows: site 1 deletes the tasks of the present account after the move of the distro, the point of no return of the move for the host

## v2.275.0 - 2026-11-22

### Added

* The Telegram channel of main and finance has an alert, AgentChannelFailing: the platform Vector counts the error lines of the channel on the node, as fixed patterns (a failed or hung send of a reply, a poll that stops, a restart of the channel), and only the count leaves the node; the log of the agent container still does not ship

## v2.274.0 - 2026-11-22

### Added

* agents: the code host server of main (task 6.19, the read step of M7b, one pull request after the other three servers; the config of the main agent lists its 17 reads). Deployed, not live for use until its live rows pass. Its read credential is a stopgap of the owner: a read-only token for this repository only, until the token job of the App of the main agent writes the Secret. The GitHub MCP server v2.2.0 (pinned by digest) in agents-main, read only, in lockdown mode, with its 17 reads by name, behind a proxy container that sets the token of the Secret github-mcp-token on each request and drops each mode header. The route main-github on the central MCP gateway takes the key of the main agent only, names the 17 reads from the dump tools/github.json, and a guardrail refuses a call for any repository but the ops repository. The pod reaches the API of the code host and its raw content host only. The gateway probes of main and finance also try it straight each day.

## v2.273.0 - 2026-11-22

### Changed

* windows: site 1 checks its .wslconfig again and no longer writes it, the convention of a host with a cluster, after the first apply of the phase done of the move

## v2.272.0 - 2026-11-22

### Changed

* site 1 Windows host: the allow list of sshd names only the account that Ansible logs in as (step 6 of the account change, before the removal of the tasks of the present account)

## v2.271.0 - 2026-11-22

### Changed

* windows: the done switch of the move of the distro on site 1: the account that owns the distro is the new account, the copies of the start tasks stay enabled, the play no longer registers the tasks of the present account, and the host check reads the moved disk file and its rules

## v2.270.0 - 2026-11-22

### Added

* tools: a device-access pod on homelab-wsl, with its own peer on the router's CI tunnel (wg03), reaches the EP-R6 and the two radios behind it over SSH and the radios' web UI, for an operator who execs into it; the router lets every wg03 peer reach the radios' SSH, only that pod their web UI, and only the runners its REST API

## v2.269.0 - 2026-11-22

### Added

* agents: the gateway probes of main and finance try the cluster, Flux and Terraform servers of main straight each day: only the gateway may reach them. A time out passes (a policy drop is silent); a refusal (no ready backend or a closed port), an answer and a name that does not resolve each fail. The probes use no key on a read route.

## v2.268.0 - 2026-11-22

### Added

* mikrotik: the router lets the desktop read the LiteAP radio (10.60.10.45) and the LiteBeam radio (10.60.10.43) over SNMP: UDP 161 from the monitoring list to those two addresses only, for snmp-exporter; SNMP is not on in the radios yet

## v2.267.0 - 2026-11-22

### Removed

* monitoring: the legacy UNMS Overview board is removed, because its data source (the UISP instance) is gone and its saved panel settings held identifying values.

## v2.266.0 - 2026-11-22

### Added

* monitoring: smokeping pings the LiteAP radio (liteap-gps) and the LiteBeam radio (litebeam-5ac) behind the EP-R6 (role radio), and the radio link has one alert per hop: RadioAPUnreachable when the LiteAP stops answering while the EP-R6 answers, RadioStationUnreachable when the LiteBeam stops answering while the LiteAP answers (RadioLinkProbeMissing when nothing probes them). The Network & Routing tab Radio gateway becomes Radio link: a state per hop (Unknown behind a hop that is down), loss and latency per hop. The airCube rejects pings and is not watched

## v2.265.1 - 2026-11-22

### Fixed

* wsl: the USB attach unit of the distro attaches with the usbip client of usbipd-win against the usbipd service of the host, as usbipd attach reads the list of services at the Windows service control manager, which grants it to interactive and service logons, SYSTEM and the Administrators only; with no elevation the Windows backup task hands the attach to the distro and the watchdog skips its USB attach; on WSL zwave-js-ui mounts the folder /hostdev, so a pod that starts before the device leaves no directory at the name of the node, and the unit removes such an empty directory before it attaches

## v2.265.0 - 2026-11-22

### Added

* agents: AgentPlatformCheckStale also covers the daily key check of the model gateway (ai/litellm-key-check): it fires when the check has not passed in 26 hours, or never passed once a run was due. Before, a key check that stopped (its CronJob suspended, or its failed Job gone with no newer run) raised no alert.

## v2.264.1 - 2026-11-22

### Security

* monitoring: a network rule for Alloy. Its OTLP receiver (4318) takes the agent pods of main, finance and pilot, the sandbox supervisors of agents-workflows and the daily test span only, so no other pod can write false series of the agents into Prometheus or a false span into Tempo. The Faro port takes the public ingress, and the HTTP server Prometheus of the site and the kubelet probes.

## v2.264.0 - 2026-11-22

### Added

* mikrotik: the router lets the desktop ping the LiteAP radio (10.60.10.45), the LiteBeam radio (10.60.10.43) and the airCube (10.60.10.46) behind the EP-R6: ICMP from the monitoring list to those three addresses only; their SSH and web UIs stay VPN-only

## v2.263.0 - 2026-11-22

### Added

* agents: the alert AgentTurnNotEnded fires when a personal agent took a message and has not ended its turn for 30 minutes (its count of messages in is higher than its count of messages that ended, from the metrics of the product). It catches a turn that never ends (a model call, a tool or a streamed part that hangs), not a final reply that hangs after the turn ended; a quiet evening has no message and does not fire. The AI board has the row Messages with no end on its Messages tab, and the runbook has a section.

## v2.262.0 - 2026-11-22

### Changed

* agents: each CronJob of a trust domain and of ai deletes a finished Job after 26 hours, a failed one too (ttlSecondsAfterFinished). AgentPlatformCronJobFailed and the AI board still see a failure until the next daily run ends, and KubeJobFailed clears by itself instead of firing until three newer runs fail. The one-off Jobs that Flux applies keep no time to live, so Flux never runs them again.

## v2.261.0 - 2026-11-22

### Added

* agents: the cluster server of main (task 6.19, the read step of M7b). The Kubernetes MCP server v0.1.3 (pinned by digest) in agents-main, read only, as its own service account with the role view in each namespace of this cluster but agents-finance and ai and the list of the namespaces, with Secrets and exposed-secret reports refused. The route main-cluster on the central MCP gateway takes the key of the main agent only and names eight reads, from the dump tools/cluster.json. Only the gateway and the kubelet reach the pod; it reaches the API server and DNS for its name only. Renovate waits 7 days and a person reviews each update. A test, observability/tests/test_main_read_servers.py, checks the files and runs the pinned gateway and server in containers.
* agents: the Flux server of main (task 6.19, the read step of M7b). The Flux MCP server of the Flux Operator v0.63.0 (pinned by digest, unmodified) in agents-main, in its read-only mode, as its own service account with the read bindings of the cluster server, and with no DNS. The route main-flux on the central MCP gateway takes the key of the main agent only and names eight reads, from the dump tools/flux.json; the diff and get_flux_instance stay out. The reconcile is a later step.
* agents: the Terraform server of main (task 6.19, the read step of M7b). The Terraform MCP server 1.4.1 of HashiCorp (pinned by digest) in agents-main with the registry toolset and no token, no account at the API server, and egress to the public registry only. The route main-terraform on the central MCP gateway takes the key of the main agent only and names the nine registry tools, from the dump tools/terraform.json.
* agents: the read step of M7b goes live for the main agent (task 6.19): the cluster, Flux and Terraform servers. The config of the agent has one server entry for each read route, with the kept tools in its filter and each tool by its exact name in the sandbox allow list. The agent pod reaches the MCP gateway on its MCP port, and the gateway admits it. The hash of the key of the main agent (MCP_GATEWAY_KEY of agent-secret, the key of its vault route) on each read route comes from bin/main-mcp-key, which the lead runs before the push: it keeps the key, sets the hash, and prints names and ok only. A test fails while a read route has another hash than main-vault. The code host server follows in one more pull request.

## v2.260.3 - 2026-11-22

### Fixed

* Flux admission: classlist.py and the render of the test harness take the API group of a document exactly (the part of apiVersion before the "/"), through one helper, in place of a prefix or substring match. The class lists, the policy and the cluster lists stay the same; a test checks the helper with groups that a prefix or substring match would take

## v2.260.2 - 2026-11-21

### Fixed

* windows: site 1 states its UDP relays and its bound USB device, so the play of the move window renders the same configs of the procedure as the play of the host, with the copies of both relays among the start tasks of the new account

## v2.260.1 - 2026-11-21

### Fixed

* ci: the observability contracts job has 25 minutes again instead of 15. Its runs of 2026-11-21 took 690 to 885 s, and two stopped at the limit; the split of the long tests had brought it down to 15.

## v2.260.0 - 2026-11-21

### Added

* monitoring: the EP-R6 (sce-ep01), the gateway of the radio network, is watched. smokeping pings it once a second (role edge), and RadioGatewayUnreachable fires when it loses over half its pings for 4 minutes while the router and SW03 answer (RadioGatewayProbeMissing when nothing probes it). The Network & Routing board has a Radio gateway tab: its reachability as a state, loss and latency beside the router hop, the router's VLAN 140 traffic and its Internet traffic from flow export. The Internet board's probe panels show only the Internet targets and the router hop

## v2.259.0 - 2026-11-21

### Added

* agents: the main agent gets its vault (task 3.37, step D): the claim vault-ro of the main share at /vault in the gateway, its repair step and its index step, read only on the claim and each mount with no subPath; the index reads /vault (Markdown) and polls; the six read tools of the vault through the route main-vault of the MCP gateway with MCP_GATEWAY_KEY (the three write tools come with the seed of the share, which stops at a share that is not empty), and the values check stops the start when its hash is not the one of the client main; the egress of the agent to the gateway and the ingress of the gateway from it. A merge starts one new agent pod.

## v2.258.0 - 2026-11-21

### Added

* mikrotik: the router lets the desktop ping the EP-R6 (sce-ep01), the gateway of the radio network: ICMP from the monitoring list to 10.10.140.140 only, so smokeping can watch it. Its SSH and web UI stay VPN-only

## v2.257.0 - 2026-11-21

### Changed

* site 1 Windows host: Ansible logs in as the administrator account (step 4 of the account change). The desktop user, the tasks and the disk path stay with the present account by their own values

## v2.256.5 - 2026-11-21

### Security

* Flux: the class Kustomization flux-substitution makes the reduced Secret cluster-secrets-apps on both sites, with only the keys of cluster-secrets that the children outside the "ask first" class use; no child reads it yet. CI checks that each variable of a child outside the class is a key of it or a setting of the site

## v2.256.4 - 2026-11-21

### Security

* Flux admission: the read of the audit result before Deny has a positive control. The API server counts a check of a policy only when it refuses, so the live check bin/flux-admission-check (now as both Flux controllers) adds a known count on each site; a harness test shows that an allowed check adds nothing. The runbook reads the highest count in the window, so a restart of the API server does not hide one, and adds a replay of the rendered chart output of both sites

## v2.256.3 - 2026-11-21

### Security

* Flux admission: the daily CronJob flux-admission-objects on both sites lists each admission object (webhook configurations, admission policies and bindings) and fails when a Flux child outside the "ask first" class owns one, which the admission policy can not refuse. It reads the class from the live policy, with a read-only account; a failure raises KubeJobFailed, and the AI board shows it on its Checks tab

## v2.256.2 - 2026-11-21

### Security

* Flux admission: the rule operator-pods refuses, for a Flux child outside the "ask first" class, the objects from which an operator makes a pod of its own template (runner sets, sandboxes, a database pooler, and a database cluster with a privileged container or an added capability). The test harness takes its Kubernetes version from the upgrade file of the clusters, and the test that the class lists equal git also runs on a change of the Owner label script

## v2.256.1 - 2026-11-21

### Security

* Flux: kustomize-controller runs with --no-remote-bases=true on both sites, so a kustomization can not bring in a remote base or resource; no build of the clusters uses one, and a CI test keeps it so

## v2.256.0 - 2026-11-21

### Changed

* windows: site 1 turns on step 3 of the account change (wsl_move, phase move) with the session check, the start of WSL with no elevation and the keepalive, with the folder of the move on E:, the volume of its disk file

## v2.255.0 - 2026-11-21

### Changed

* site 1 Windows host: the encrypted host variables hold the values of the move of the distro (the present account, the new account, the present folder, the WSL release of the rehearsal), and the allow list of sshd names both accounts (step 2 of the account change). sshd changes only with its explicit apply

## v2.254.2 - 2026-11-21

### Security

* Owner label and the Flux admission policy: the folders of hajimari, frigate, zwave-js-ui and qbittorrent join the "ask first" class, because each needs a right of the class (cluster RBAC, or a privileged pod with a host path or an added capability). With them in the class the policy refuses no live object

## v2.254.1 - 2026-11-21

### Security

* Flux: the admission policy flux-child-scope (kubernetes/apps/flux-system/admission, docs/operations/flux-admission.md) limits what a Flux child outside the "ask first" class may apply: no cluster object, no object in a namespace of the class, no RBAC, no Flux Kustomization, no privileged pod spec, and no change of an object of the class; Kustomizations and HelmReleases of the class must come from their parents in git. Its lists come from git (classlist.py, checked in CI), its rules run on a throwaway API server in CI, each rule switched off once to show its test fails, and both sites keep its audit and deny counts. The binding starts with Audit and Warn

## v2.254.0 - 2026-11-21

### Changed

* windows: site 1 takes the phase file of the move of the monitoring data: the timer of the distro mounts the file of 19 GiB at the point each minute, and the mount task of the VHDX is disabled and stays as the way back

## v2.253.0 - 2026-11-21

### Changed

* windows: site 1 takes the phase ready of the move of the monitoring data (observability_storage_mount): the distro makes the file of 19 GiB once, its timer stays off, and the VHDX keeps the data

## v2.252.0 - 2026-11-21

### Changed

* ai: the browser tool (playwright-mcp) runs at homelab-wsl only, with one replica, beside the central MCP gateway and the fetch proxy of the main agent. homelab-wsl-02 no longer deploys it, its Service is local to the cluster (not global in the mesh), and its network policy names this cluster only. The AI board has it at homelab-wsl, and Not applicable at homelab-wsl-02. It still loads no page: no route of the gateway names it, and the fetch proxy lets in the agent pod of main only.

## v2.251.0 - 2026-11-21

### Added

* site 1 Windows host: the encrypted host variables hold the password of the administrator account, for step 1 of the account change (the same key as site 2). No role reads it

## v2.250.1 - 2026-11-21

### Security

* Owner label: a pull request whose author is not the owner, Renovate or the App of the main agent, one with a push of anyone else into the branch of another author (since the branch was last created) or a commit of a bot other than Renovate, and one of the App that touches a path of the "ask first" class all wait for owner/approved from the owner. Such a pull request fails when its base is not main or when its title, body or a commit message holds a skip instruction of GitHub Actions, and a label counts only when it is newer than the last change of the base; the check runs again on each edit. The class also holds the workstation entry files and the other controls of the cluster. The App is not set yet, so each of its pull requests waits. A new mode, owner-label-check.py --trust, answers the gates of the CI; it needs the base main and the newest push of the branch at the head. AGENTS.md keeps a pull request that waits for the owner as a draft. docs/operations/ci.md, "Agent pull requests".

## v2.250.0 - 2026-11-21

### Added

* site 1 monitoring: the data of Prometheus can move from the VHDX that a Windows task attaches into a file of 19 GiB on the disk of the distro, ext4, mounted by a loop device at the same point (role wsl_observability_file, phases vhdx, ready and file of observability_storage_mount). With the file, the mount task is disabled and stays with the VHDX as the way back, and a compaction leaves a disabled task disabled

## v2.249.1 - 2026-11-21

### Security

* ai: the browser tool (playwright-mcp) gets a network policy. In: the MCP port from the central MCP gateway and the kubelet. Out: the fetch proxy of the main agent and DNS for its name only. Each peer is named at both WSL sites, because the app moves between them. Its internal ingress goes, with its name on the router and its entry in the ingress probe of the evidence collector. A new doc, docs/apps/playwright-mcp.md, and a test, observability/tests/test_browser_pod.py.

## v2.249.0 - 2026-11-21

### Added

* windows: the USB attach runs with no elevation with wsl_start_unelevated (-NoElevation): the play binds each device as admin, and the task attaches with no bind and logs to a folder that its account may write. In the move of the distro it has a copy for the account that owns the distro, registered disabled while the move runs, as the UDP relays have, and the way back enables the task of the present account again. The play no longer refuses the switch on a host with a USB device

## v2.248.0 - 2026-11-21

### Added

* windows: the window procedure of the move of the distro has the check of a session of the owner (wsl_move_session_check). With it on, window step 1 stops with nothing changed while the present account has a session at the console or over Remote Desktop, and the way back, in the procedure and in the boot task, does not restart while one is on, also after a failed check. A read that fails counts as a session. With it off the procedure reads no session, as before. The guards of the window play make the same read, from the copy of the procedure on the host

## v2.247.3 - 2026-11-21

### Fixed

* windows: the boot task of the way back of the move (and of its rehearsal) names its boot trigger with no default child, so a check run no longer reads it as a change: the Task Scheduler leaves an Enabled of true out of the export that the task module compares

## v2.247.2 - 2026-11-21

### Fixed

* observability: the schedule state of the AI board matches each namespace of a declared schedule, so litellm-key-check (namespace ai) reads its last good run and no longer no evidence. AgentDailyArchiveFailed reads the exit code of the backup container (1 to 127), so a stop of the node (exit 143 from SIGTERM, or 255) no longer fires it.

## v2.247.1 - 2026-11-21

### Fixed

* observability: the token counter of the model bridge (homelab_model_bridge_tokens_total) lives as long as the platform log agent. The exporter of Vector dropped it 1 to 2 minutes after the last call line, and its sample kept the time of the line, so the token panels of the AI board counted the first call as 0 and later calls only in part. A static_metrics source now adds 0 to each declared series at the start and every 30 seconds, and each sample has the time it was counted.

## v2.247.0 - 2026-11-21

### Changed

* agents: the main agent sends the metrics of the product to Alloy, after the finance agent (step 3 of task 5.23). Its pod restarts once for the change of its config.

## v2.246.0 - 2026-11-21

### Added

* observability: the AI board shows the use of the Claude plan first on its tab Limits and security: the share used of the five hour window and of the week, the time to each reset, the state of each window, the use over time, the age of the last reading, and the tokens of each trust domain by kind. The platform log agent of Vector turns the plan_limit and call lines of the model bridge into series (the window only on the plan series, so the newest line of any trust domain wins; at most 60 series), the records homelab:ai_plan_* keep the newest reading until its window resets, and the warning ModelPlanNearLimit goes to the chat (it never pages) when a window is near or at its limit.

### Changed

* ai: the model bridge 0.3.0 logs a plan_limit line for each limit window of the Claude plan and the token counts of each answer on its call line. The AI board and the alert ModelPlanNearLimit read them.

## v2.245.0 - 2026-11-21

### Changed

* agents: the finance agent sends the metrics of the product to Alloy, after the pilot agent (step 3 of task 5.23). Its pod restarts once for the change of its config.

## v2.244.0 - 2026-11-21

### Added

* observability: the AI board shows the metrics of the personal agents (step 3 of task 5.23): messages in, turns by outcome and trigger, replies sent and failed, the time to send a reply, stuck sessions and failovers. The pilot agent sends its metrics first (finance and main follow, each in its own change), and Alloy keeps 8 instruments, the listed labels of each and their declared values (other for each other value), cuts the resource to the service name (undeclared for each other name), and writes to the OTLP path of Prometheus. The network rule of Prometheus lets Alloy of the same site write there and no reader, and the daily CronJob prometheus-rule-check proves that a reader can not write.

## v2.243.0 - 2026-11-21

### Added

* observability: the network rule of Prometheus names its readers (step 3 of task 5.23). On the query port, Grafana of each site, the Pyrra UI, the internal ingress and Prometheus itself may send GET on each path and POST on the query paths; the evidence of the other site and the LAN bridge get the two paths that they call; Thanos Query of each site gets the sidecar port. Nobody may write or call the admin and lifecycle paths. A test fails when git names a new reader of Prometheus that the rule does not know.

## v2.242.0 - 2026-11-20

### Added

* windows: once the move of the distro is done, the compaction runs as SYSTEM, reads the owner and the rules of the disk file before the pause and before diskpart, and stops WSL with a stop task of the account that owns the distro

## v2.241.0 - 2026-11-20

### Added

* observability: the network rule of Prometheus, first form (step 3 of task 5.23). The CiliumNetworkPolicy prometheus sends each request to the query port through the proxy of Cilium and lets each one pass, with default deny off, so Hubble shows the method, the path and the source of each request before the next form names the readers.

## v2.240.1 - 2026-11-20

### Fixed

* windows: the configs of the move take the values of the registration of today as a mapping, not as the text that win_powershell gives for an object below its output depth, and an apply after a done window writes those values into the registration of the new account and removes the value Length that an import with that text writes; after the deletion of the tasks of the present account, the copies of the scripts and the configs of the procedure follow the role again

## v2.240.0 - 2026-11-20

### Changed

* windows: site 2 deletes the tasks of the present account after the move of the distro: each WSL task and window task of that account and the boot task of the way back, only in the phase done, after a window that ended done, and while the allow list of sshd no longer names that account

## v2.239.0 - 2026-11-20

### Added

* litellm, observability: the series of the model gateway (step 2 of task 5.23). The Prometheus callback with a keep list (5 metrics; the key name, the name asked for, the provider and the status) and a cap of 100 label sets for each metric; a scrape with a key of its own (metrics-scrape: no model, the route /metrics only, pinned in ensure_keys.py), which writes undeclared for a key name that is not declared; the daily CronJob litellm-key-check, which fails when that key can call a model name; the Model calls tab of the AI board (calls, the share that failed, the time of a call, tokens by key, name and day); and the alert ModelGatewayCallsFailing with its runbook section and promtool cases.

## v2.238.0 - 2026-11-20

### Added

* observability: the AI board in Grafana (tnwks-ai, step 1 of task 5.23), from the data of today. A declared list of the AI parts, scheduled checks, result lines and snapshot policies, with a state for each (a part that is gone stays a row, as Unknown; site 2 declares each part it does not run as Not applicable); the AI tile on the Homelab wall and its row in Needs attention; tool calls of the MCP gateway without the GET with 405 of a client start; the plan limit, instruction file changes and mover denials; the time of the newest restore test result. Each of the 18 AI alerts opens the AI board, and the certificate alert of the model gateway has its runbook section and promtool cases.

## v2.237.0 - 2026-11-20

### Changed

* Site 2, Windows host: the allow list of sshd names only the account that Ansible logs in as (Design Doc 0004, section 9, step 6). The encrypted `windows_sshd_allow_users` of the host loses the present account of the move; the apply on the host stays the explicit one of the role windows_sshd.

## v2.236.0 - 2026-11-20

### Changed

* windows: site 2 turns on the done switch of the move of the distro: the account that owns the distro, the disk file of the compaction and the host check, and the read of its rules follow the phase, and the play stops when they do not

## v2.235.0 - 2026-11-20

### Added

* agents: the gateway probe of main has its key (PROBE_KEY, client main-probe of the route main-vault), and the gateway config has its hash in place of the placeholder. The key checks of the probe run from the next scheduled run.

## v2.234.0 - 2026-11-20

### Added

* agents: the nightly snapshot of the main share (task 3.37, step C): the SnapshotPolicy and SnapshotSchedule vault in agents-main, the form of finance, from the read-only claim vault-ro to the repository agents-main, with the retention of the state volume.

## v2.233.0 - 2026-11-20

### Added

* agents: the gateway probe covers the vault route of main (task 3.37, step B): platform-check-mcp-gateway-probe in agents-main (05:47) checks the route main-vault with its key main-probe (a placeholder until the values step, read from an optional Secret), and the probes of finance and main each check that their key gets a 401 on the vault route of the other trust domain and that the other vault server is out of reach. The probe script runs for a vault route only when a domain has no other route.

## v2.232.0 - 2026-11-20

### Changed

* agents: the restore test of the finance share passed on 2026-11-20 and is out of the list again; Flux deletes its Restore, its Job and its scratch claim. The files stay, with the snapshot name as a placeholder.

## v2.231.0 - 2026-11-20

### Added

* agents: the vault server of main (task 3.37, step A): the pod of finance in agents-main on the claim vault-rw of the main share, the route main-vault on the central MCP gateway with the key of the main agent (a placeholder hash until the values step) and the nine tools, the egress of the gateway to it, and main's DNS rule without the vault server. No pod of main calls the route yet.

## v2.230.1 - 2026-11-20

### Fixed

* windows: in the move of the distro, the copy of the watchdog for the standard account skips the check of the WSL service, which that account may not query and the SYSTEM task does; both accounts may write the inbox of the host check while the move runs; and the check play reads the default user by the rule of WSL, wsl.conf first, with the read in the distro

## v2.230.0 - 2026-11-20

### Added

* agents: the restore test of the finance share (finance/vault-backup/restore-test/), listed for one run: the Restore of the newest Succeeded snapshot of the policy vault to a scratch claim, then a check Job that compares it with the live share, read only, by path and SHA-256 of each file, with counts of files, notes and bytes, and logs flags and counts only. The next change takes it out of the list.

## v2.229.0 - 2026-11-20

### Changed

* agents: the main agent thinks at level high by default (thinkingDefault), by the owner's word. The copy of the host setting was off, which the model of the platform does not take: the product used low and wrote a warning in each run.

## v2.228.0 - 2026-11-20

### Changed

* agents: the daily heartbeat check (06:33 UTC) checks the main agent too, after its first completed heartbeat in Tempo (2026-11-19 15:05 UTC): SERVICES is openclaw-agents-pilot,openclaw-agents-finance,openclaw-agents-main, and a test holds the list to the running agents with a heartbeat. The runbook shows vault-notes=none for a restore with no vault archive.

## v2.227.0 - 2026-11-19

### Changed

* Site 2, Windows host: Ansible logs in as the administrator account (Design Doc 0004, section 9, step 4). The encrypted `ansible_user` of the host changes; the jump, the distro and the paths keep the present account by its own variable, so no role changes a thing on the host.

## v2.226.0 - 2026-11-19

### Added

* windows: the window play of the move of the distro, with its precheck, its start of a window or a way back, its status, and the check play of step 6

### Fixed

* agents: the daily backup of the main agent runs on a copy of its config with regular files, since the backup command of the product refuses an include file that is a link, as each file of the projected config volume is

## v2.225.1 - 2026-11-19

### Fixed

* agents: the first start of the main agent stopped at the official plugins step, because the step of the image copies only openclaw.json and the config of main includes the group file. The pod now gives the step one file with the include filled in.

## v2.225.0 - 2026-11-19

### Changed

* windows: on site 2, Ansible logs in as the administrator account, the account that owns the distro comes from the values of the move, and the jump logs in to the distro as its user (step 4 of the account change)

## v2.224.0 - 2026-11-19

### Added

* agents: step 5 of the main agent: the agent pod starts for its test run beside the host, on the test bot that moved from the pilot, and the restore Job is out again (docs/apps/agent-platform-runbook.md, "The test run beside the host").

## v2.223.0 - 2026-11-19

### Changed

* agents: the test bot of the pilot moves to the main agent, for its test run beside the host: its token in the Secret of main, and the Telegram channel of the pilot off (one bot has one poller); the pilot runs on for the platform checks with no chat channel

## v2.222.0 - 2026-11-19

### Added

* agents: the finance agent runs the plugin active-memory, with the settings of the host (direct and group chats, query mode recent, prompt style recall-heavy, a limit of 25 s), on the model of its session through LiteLLM until the model gateway has a recall model for finance. The finance agent pod restarts once. The config of the main agent gets the same settings for its first run; it has no pod yet.

## v2.221.0 - 2026-11-19

### Added

* agents: web fetch for the main agent through a fetch proxy in agents-main (Envoy, no secret): CONNECT to a host name on port 443 only, no address inside after its own lookup, a log line with the host for each request, kept on the pod and out of Loki; the agent pod reaches the proxy and no web site directly

## v2.220.0 - 2026-11-19

### Added

* windows: site 2 turns on the move of the distro (step 3 of the account change), with the start tasks of the account that owns the distro, and the play stops before any change while a value of the move is missing

## v2.219.0 - 2026-11-19

### Added

* agents: the finance agent reads its share: the claim vault-ro at /vault, read only on the claim and on each mount, in its memory index with polling, and the nine vault tools through the route finance-vault with its key. The finance agent pod restarts once (docs/apps/agent-platform.md, "The vault shares").

## v2.218.0 - 2026-11-19

### Added

* agents: the seed of the finance share, a one-time Job: the secret scanner of CI on the copy of the vault on the state volume (read only), one line for each finding with an id for the owner, and a copy into the empty share only when the owner decided each finding (no link, no config folder of the notes app, no trash, no .git; files 0600); one result line with counts

## v2.217.0 - 2026-11-19

### Added

* agents: a nightly snapshot of the finance share to the Kopia repository of the finance trust domain, read through the claim vault-ro, with the retention of the state volume (docs/operations/backups.md).

## v2.216.0 - 2026-11-19

### Added

* agents: the daily gateway probe of finance also checks the vault route: no key and a random key get a 401, the probe key lists the nine tools and reads the facts of the root of the share only, a write and a move are refused, and a pod that is not the gateway can not reach the vault server (docs/apps/mcp-gateway.md, "The probe").

## v2.215.0 - 2026-11-19

### Added

* agents: the vault server of finance, the one writer of the finance share, on the claim vault-rw in agents-finance, and its route finance-vault on the central MCP gateway with the key of the finance agent and nine file tools. Only the gateway reaches the server, and the server reaches nothing. The gateway now waits 60 s, not 10 s, for the finance app too. The finance agent does not use it yet (docs/apps/agent-platform.md, "The vault server").

## v2.214.0 - 2026-11-19

### Added

* agents: step 4 of the cutover of the main agent: the three Secrets of the owner and the restore Job of the copy of the state, with no vault. The inbox is out again, and no pod starts.
* agents: the main agent gets the progress card tool, and its sessions see each other as on the host. It first runs beside the host agent as a test, with a second bot: the bot token is a placeholder until the owner puts that token in, so no pod starts before it (docs/apps/agent-platform-runbook.md, "The test run beside the host").

## v2.213.0 - 2026-11-19

### Added

* windows: the role windows_wsl_move, step 3 of the account change: the account that owns the distro, its folder, and the tasks of the move window, off on every host

## v2.212.0 - 2026-11-19

### Changed

* Windows hosts, sshd: AllowUsers names each account of windows_sshd_allow_users (default: the account that Ansible logs in as), so a host can allow both accounts while it moves its distro to another account (Design Doc 0004, section 9, step 2). The bootstrap script takes one account or more in -AdminUser, in order, each once, and refuses a name that sshd reads as a pattern or a host. The role and task wsl-host:sshd pass the list, and each refuses a list that does not hold the account that Ansible logs in as. With no list, the config is the same as before.

## v2.211.0 - 2026-11-19

### Changed

* Site 2, Windows host: the encrypted host variables get the values of the account change (Design Doc 0004, section 9): the list of accounts for AllowUsers, the present account, the new account, the folder of the disk file of today, the WSL version of the rehearsal and the password of the administrator account. No role reads a new value yet.

## v2.210.0 - 2026-11-19

### Added

* agents: the agent pod of the main agent at zero replicas, in the form of the finance agent: one Telegram account with the direct chat of the owner and one group (its chat id from a Secret of the owner), web search, the message tool and the built-in tools of the product. No MCP tool, no vault and no scheduled job yet. No pod starts until the steps of the cutover (docs/apps/agent-platform-runbook.md, "The cutover of the main agent").
* agents: the three Secret files of the main agent, not listed until step 4 of its cutover, and the key agents-main at the model gateway (models agents-main and agents-main-embeddings). The search key is a placeholder until the owner makes it at the vendor.

## v2.209.0 - 2026-11-19

### Added

* litellm: the entry agents-main-embeddings, the embeddings of the memory search of the main agent on the model of the host, so no vendor gets that text. No key lists it until the owner adds the key agents-main.

## v2.208.1 - 2026-11-18

### Fixed

* agents: the read-only volumes of the vault shares (vault-ro, in agents-main and agents-finance) mount read only for each pod, with the option ro. The kubelet takes the read-only flag of a PersistentVolume from the pod, not from nfs.readOnly. No pod mounts them yet, and no pod restarts (docs/apps/agent-platform.md, "The vault shares").

## v2.208.0 - 2026-11-18

### Added

* windows: each UDP relay gets a copy for the account that owns the distro, and both accounts may write the logs of the relays while the move runs (off on every host)

## v2.207.0 - 2026-11-18

### Added

* windows: the address task of the LAN bridge gets a copy for the account that owns the distro, and both accounts may write the address file while the move runs (off on every host)

## v2.206.0 - 2026-11-18

### Added

* agents: the volumes and claims of the vault shares, step d of the vault shares. Two static PersistentVolumes for each share (read and write with a hard mount, read only with a soft mount) and their two claims, vault-rw and vault-ro, in agents-main and agents-finance. Each claim binds to its volume at once; no pod mounts them yet, and no pod restarts (docs/apps/agent-platform.md, "The vault shares").

## v2.205.0 - 2026-11-18

### Added

* windows: the watchdog and the keepalive get copies for the account that owns the distro, under their own names, with the phase of the move per host (off on every host); the folder module takes a list of writers, and a task whose state is its own is not started while it is disabled

## v2.204.0 - 2026-11-18

### Added

* nfs-server: the vault share of each trust domain with a vault, step b of the vault shares. Two claims (nfs-vault-main, nfs-vault-finance) and two exports (/vault-main, /vault-finance): read and write for the three nodes of homelab-wsl only, each client squashed to uid 1000. The exports of today keep their ids and settings. The server restarts once (docs/apps/agent-platform.md, "The vault shares").

## v2.203.0 - 2026-11-18

### Changed

* kube-prometheus-stack (homelab-wsl): the scrape of the API server keeps the checks of the two admission policies of NFS volumes (nfs-pod-volumes, nfs-persistent-volumes) with enforcement_action "audit" or "deny", beside the denials of the mover policy. So the count of the volumes they would refuse is in Prometheus while they audit.

## v2.202.0 - 2026-11-18

### Added

* nfs-server: two admission rules list which pods and PersistentVolumes may mount the NFS server of homelab-wsl (nfs-pod-volumes: a namespace, server and exact path for each user of today; nfs-persistent-volumes: the read and write and the read-only volume of each vault share, each with its one claim). They audit and warn only for now; a later change turns them to Deny. A throwaway API server tests the rules (admission/tests/run.sh), and a test checks that each NFS volume in git is on the list (docs/apps/agent-platform.md, "NFS volumes and admission").

## v2.201.0 - 2026-11-18

### Added

* Security, the Windows host check: the check wsl-disk-rules (Design Doc 0004, section 7, row 27), with the severity high, in line 3.3 of the registry for the Windows host type. Each 6 hours as SYSTEM, with no privilege, it opens the disk file of the WSL distro through the open of the move procedure and reads its owner and DACL on that handle. It passes only when the owner is the account that owns the distro, the DACL is protected, and each entry is an allow entry with full control and no flag: one each for SYSTEM, the Administrators and that account, any number for a VM, and at most one for the capability SID of WSL. A missing file and a file that can not be read are each a fail. The check is on for a host from its done switch (wsl_start_unelevated_done), and gives no result before it. The series homelab_security_host_wsl_disk_rules_reason names each rule that the file breaks, from a fixed list. A fail starts SecurityFindingNew.

## v2.200.1 - 2026-11-18

### Fixed

* Windows hosts, the window procedure of the move: each read of the rules, the read of the group and the set of the owner of a file by SYSTEM open the file through one open (TnwksSecurity.Open). It opens the entry at the path itself (FILE_FLAG_OPEN_REPARSE_POINT), with identification only for a server behind the path, and takes it only when it is a file or a directory of a disk, not a reparse point, with one name, and with a final path that is the path itself. Each refusal closes the handle before any rule is read or written. Tests check the flags of the open and each refusal with stand-ins for the calls of Windows.

## v2.200.0 - 2026-11-18

### Added

* Windows hosts, the window procedure of the move: reads of the icon of the distro (shortcut.ico) before each import, for the account of that import, with no delete. Window step 1 reads both folders and stops with nothing changed; after window step 5 no icon may be left in the old folder; before window step 6 the folder of the new account is read again; before way back step 5 the old folder is read, and a fail stops the way back before the import. The read of its attributes takes the entry itself and follows no link: a directory or a link is refused, and its rules are not read. A read passes with no entry, or a plain file with none of the attributes read-only, hidden, system, directory, reparse point and encrypted, no deny, object or conditional entry, and a rule that gives the account of the import the right to write it (and to delete it where its unregister does). Tests use test icons for each read. Window step 7: a process of the present account with no path that SYSTEM can read counts by the names of the WSL commands of Windows too.

## v2.199.1 - 2026-11-18

### Fixed

* CI: the PowerShell tests of security/ (the pwsh mark, run alone as the powershell job runs them) take one pytest-xdist worker for each CPU, so the job stays within its time limit. A run of the other tests, or of both marks, stays in one process, and -n on the command line still decides.

## v2.199.0 - 2026-11-18

### Added

* Windows hosts, the window procedure of the move: before the present account leaves Administrators at window step 7, and again after it, a read as SYSTEM through the API of the Host Compute Service (computecore.dll, HcsEnumerateComputeSystems) that no compute system runs, of any owner or type, read again for a VM that a stop ended (at most 10 reads, 2 seconds apart); then the check that no process of the WSL package of the present account runs, by its image path in the install folder of the package or a WSL command of Windows in System32. A fail before the leave sends the window back while the account is still a member; a fail after it sends the window back, and way back step 1 puts the account back. The check is on with no key; only a config of the rehearsal may turn it off (vm_check: false), as the host's own distro runs during a rehearsal. A test shows that each mode of the procedure, the boot run and a forward run that goes back among them, runs the group step of the way back with a config that is not the rehearsal's.

## v2.198.1 - 2026-11-18

### Fixed

* Windows hosts, the window procedure of the move: the fail line of each step that runs a task of an account (the stop, the unregister and the import of the window, the unregister and the import back of the way back) holds the exit code of the task and the error text of its command from the result of this run, or says that there is no result of this run. A step takes the result of a task of an account only from a run that ended in this run, with a time from the start of the run to the moment of the read. Each fail line is one line and bounded, as is the line of a task with its result. The checks of the rehearsal read each line of the procedure log by its start. Tests show that a result of an earlier run, a task that starts and never runs, and a start that Task Scheduler refuses each fail their step with that text.

## v2.198.0 - 2026-11-18

### Added

* agents: step 2 of the move of the main agent: the snapshot policy and schedule of its state volume, to the backup repository agents-main. The first snapshot runs at once, of the empty volume.

## v2.197.0 - 2026-11-18

### Changed

* Windows hosts, the rehearsal of the move: the rehearsal ended, and its switch (wsl_move_rehearsal) is off on the site 2 host until the next rehearsal. A run of the playbook no longer includes the role there. The check play of the rehearsal now says of each host with the switch off that it checked nothing there, and fails when no host of the run has the switch on; after a rehearsal, -e wsl_move_rehearsal=true gives the switch for one run.

## v2.196.0 - 2026-11-18

### Added

* Backups: the ClusterRepository agents-main, with the grant of agents-main only. Kopia creates the repository at its first bootstrap, with the password of kopiur-repository-agents-main.

## v2.195.0 - 2026-11-18

### Added

* Backups: the session refresher of the main repository runs (its role and profile in the wsl cluster settings, and its directory listed for Flux). The ClusterRepository comes in the next step.

## v2.194.1 - 2026-11-18

### Fixed

* Windows hosts, the rehearsal of the move: the stops of the procedure at setup give their test config a boot request path of its own, in the test folder. Before, that path was the one in the state folder of the rehearsal, so with a boot request there the boot cases of the stops would read it, could take an account out of Administrators, and would remove it at their end. No path of the test config is now in the state folder.

## v2.194.0 - 2026-11-18

### Added

* Backups: the directory of the backup repository of the main trust domain (kopiur-system/agents-main-repository: its refresher, its RBAC, its session Secret with placeholders, and its ClusterRepository agents-main with the grant of agents-main), in git and not listed for Flux, and the two settings keys RA_AGENTS_MAIN_BACKUPS_* as marked placeholders. The two SOPS files, the session, the repository and the backup policy of the main agent come in later steps, in the order of docs/operations/backups.md. A test holds each repository of a trust domain to its Terraform entry and to that order.

## v2.193.0 - 2026-11-18

### Added

* Backups: the role and the prefix of the backup repository of the main trust domain (entry agents-main of rolesanywhere_backups_prefixed, prefix kopiur-main/, exclusive to its role in the bucket policy), step 1 of its repository. The certificate, the password, the session and the repository come in later steps, in the order of docs/operations/backups.md, "The main repository".

## v2.192.0 - 2026-11-18

### Added

* Windows hosts, the rehearsal of the move: a boot run of the group test. The phase interrupt with -e wsl_move_rehearsal_boot_group_steps=true writes a boot request after its window, and the boot run then takes the group steps and the group test from it, for that one run: A joins Administrators at way back step 1, after the restart, and the end of the run takes it out whatever the result, after A's stop task ended a VM that the import back started. A boot run after a power loss takes A out first. The procedure refuses the group test with the restart on, with a config of the rehearsal, group steps and a group test that are not equal (in each mode, from a request or from the config), and a boot request that is not two equal booleans; only the boot mode reads the boot request. The phase after-boot checks this case with the same switch, removes an icon of the shortcut that A does not own, compares the count of the VM processes with the baseline, starts A's start task and reads A's tokens. The check play accepts the group moments of such a boot run.

## v2.191.0 - 2026-11-18

### Added

* agents: step 1 of the move of the main agent: the state volume of agents-main and its inbox pod, on the node of the agent pods, with no product code, no secret and no network. The backup policy of the volume is in git and not applied until the backup repository agents-main exists and is Ready; the network policy of the backup movers of agents-main selects no pod until then. The runbook has a draft of the cutover of the main agent: Telegram first, Signal later with its own go, and the steps that wait for the owner

## v2.190.1 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the phase wayback takes a switch for its group steps (wsl_move_rehearsal_wayback_group_steps, on by default). With it off, the way back runs as a standard A, as the boot run does, and the phase also ends a started window that did not end. Before any change, the phase reads that no run of the window task or of the boot task goes on, and stops with nothing changed when one does. Before A imports back as a standard account, the play removes an icon of the shortcut in A's folder that A does not own, because WSL writes that icon as the importing account and a standard account can not replace an icon that the Administrators own. The phase interrupt reads that icon first and stops when A does not own it.

## v2.190.0 - 2026-11-17

### Changed

* agents: the daily heartbeat check (agent-heartbeat-check, 06:33 UTC) checks the finance agent too: SERVICES is openclaw-agents-pilot,openclaw-agents-finance. Each service gets its own tries and its own log line; one missing service fails the run and fires AgentHeartbeatMissing, and the line names the service. The docs say how to read that line and that a new agent joins the list after its first heartbeat is in Tempo

## v2.189.10 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the phase after-boot reads the accounts of the rehearsal before its read of A's disk file, which needs their SIDs. It also checks the run of the boot task right after its read of the log, so a boot run that did not end the way back stops the phase before any task of A or B starts. A new static test walks each phase, and the check play, in a run and in a check run, and fails when a task reads a fact or a result of the role that no earlier task of that phase sets for sure.

## v2.189.9 - 2026-11-17

### Fixed

* model-bridge: image 0.2.7, a call after a model call of the engine that no caller waits for is rebuilt at once and no longer waits for the call timeout (it also brings the releases 0.2.4 to 0.2.6)

## v2.189.8 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: window step 1 also stops, with nothing changed, on a disk file with a rule that the check of the way back refuses (a deny rule, explicit or inherited; more than one rule of the capability of the VM; an explicit rule of a VM SID or a capability SID that is not in WSL's form); what a folder gives a file names the file's own group for CREATOR GROUP; the read of the live parts is one of the role's scripts with its SHA-256, and each read copies it first; a failed write of the older form of A's disk file puts its owner back; the message of a copy that differs in the middle of a window says to end it with the release that started it. The account task strip-rules writes the rules of its disk file with .NET and an object that has the rules section only, so a standard account with no privilege can write them (Set-Acl tried to write the audit section too); an error that a step of the account task does not handle ends it with exit 1 and a result that holds the text of the error. The stops of the procedure keep a line on stderr of their powershell.exe as text and make their test folder inside the try; each read of a log or state line takes a line with no time or no text as empty, so a text over two lines breaks no read and no check; and the model of the output of win_powershell in the tests gives an enum, a date and an enumerable that is not a list as the module does, and a script that writes an error record fails its test.

## v2.189.7 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the phase wayback can leave out its stated case (B removes SYSTEM and the Administrators from the rules of its disk file) for one run, with -e wsl_move_rehearsal_wayback_strip_case=false; by default the case runs. The tests of the check that a task can not start also hold a run that exits 1 and writes a result, and the docs row of the phase says that the inherited rules come from the folder.

## v2.189.6 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the check that a task can not start also passes when the task shows no new run and no result of this run, as for a task that ran well in an earlier window and keeps the last result 0 of that run. Before, it held only for a task that never ran or one that Windows refused at the start.

## v2.189.5 - 2026-11-17

### Fixed

* agents: one merge gives one rollout of an agent pod (pilot and finance): the config and the scripts are ConfigMaps with a hash in their names, in the Flux Kustomization of the pod, and Reloader watches the Secrets only; the official plugins step prints the reason of a failure (OPENCLAW_DEBUG=1); the docs say what a rollout costs, and how to turn the scheduled jobs off before a revert

## v2.189.4 - 2026-11-17

### Fixed

* Rehearsal of the move: each phase takes the scripts, configs and tasks of the role from one task file, refreshed only with no started window and compared by SHA-256 before each run of the procedure; the compare against the saved set takes the owner, the protection and the explicit rules, and the play reads apart whether the inherited rules of a disk file are those that its folder gives (Windows' own inheritance function); the checks allow the capability SID of the VM and the four-part VM SID only as allow rules with full control, and refuse deny rules; window step 1 stops on a disk file without the inherited rules of its folder; A reads the folder of its disk file

## v2.189.3 - 2026-11-17

### Fixed

* Rehearsal of the move: the new account owns its moved disk file, from two role values; the procedure reads and writes file rules with the privileges of SYSTEM and restores the saved rules after the move back; the checks allow the rules of a VM only; the window phase runs again after a way back; new stated cases for an older disk file, at setup and at the start of each window, and a file that grants SYSTEM nothing

## v2.189.2 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the script in the distro goes to a new private file from mktemp, a command line too long for Windows does not run, a key that the check reads twice reads as no value (a count and one text in the result), the result of import-rootfs says whether it imported, and a test finds each -c flag of the Windows roles in any form. The stops of the procedure read an empty state file, take their config from the play, and remove their test folder also when a case fails; the way back reads the VM processes of the baseline in the form that the play keeps; the read of the boot runs keeps each run as a list; a check of after-boot is a text again; no path to a script has a doubled backslash; and new tests run the scripts of the role and feed the play's checks with what they give. The docs state what is left of the limits of this way for the real window.

## v2.189.1 - 2026-11-17

### Fixed

* Windows hosts, the rehearsal of the move: the account script passes each shell script to wsl.exe as base64 in one fixed command, so no quote or backslash of the text is lost to the argument rules of Windows PowerShell 5.1; it writes each file in the distro with a newline at its end, reads each value back by its key, and the setup asserts the SHA-256 of each written file. A second setup over the distro of A writes its files again. Each stop step of the window procedure passes only with a shutdown result of its own run, the way back compares the VM processes with the baseline by process ID and start time, the setup stops first when the controller cache has no root file system, the phase command of the docs runs the controller play too (--limit 'localhost,WSL-02'), and the comments on the worst case, the file read and the debug privilege are correct.

## v2.189.0 - 2026-11-17

### Added

* Windows hosts: the rehearsal of the move of a distro to another account (role windows_wsl_move_rehearsal, site 2 only, Design Doc 0004, section 9, step 0). Two temporary standard accounts and a small distro of their own run the scripts of the move window: the procedure as SYSTEM, with its forward steps, its way back and its boot task, and the stop, unregister and import tasks of each account. Each phase runs only when given by hand, and a restart needs the owner's go. The scripts refuse any name or path outside the rehearsal, and the live distro by name. A check play reads the groups, each moment in Administrators and the live parts, and the exit check after the removal. The task module takes keep_task_enabled_state, off by default, so a start task keeps the state that the window gives it (docs/wsl/windows-ansible.md, "The rehearsal of the move").

## v2.188.0 - 2026-11-17

### Added

* Agent platform: the MCP gateway (agentgateway 1.7.0, standalone, one config file in git, no database, no UI), shared by all trust domains in `ai`, with one route for each domain that has a tool. Each MCP call of an agent goes through it: it checks the key of the agent (a hash in the config, the value in the Secret of the agent), lets the agent see and call only the tools that its rule names, and sends the call on to the MCP server of the domain. Deny by default: no key or an unknown key gets a 401, and a tool off the rule is refused before it reaches the server. The rule of finance names the 73 kept tools of the MCP server of the finance app, from a dump of its pinned image, and a test fails when the image, the dump, the rule and the agent's filter differ. The MCP server of the finance app now takes the gateway only. One log line for each request with the client, the tool and the status, never the arguments; metrics to Prometheus; a daily probe with two keys of its own; 7 days of release age and a review for each update. The keys are placeholders until the owner's values step (docs/apps/mcp-gateway.md).

## v2.187.0 - 2026-11-17

### Added

* Agent platform: the finance app as a tool of the finance agent, read and write and never delete. A proxy in agents-finance holds the token of the agent at the app and lets through only an allow list of calls (GET, POST and PUT on the paths of the kept tools; each delete, trigger, rule write, export, attachment and setting refused). The MCP server of the app (fireflyiii-mcp 1.0.1, pinned by digest, 7 days of release age and a review for each update) calls the app through it with no token, and the sandboxes of the agent read through its read port, which lets through no write and is the only port of it that their network policy lets them reach, with no credential. The proxy drops or refuses a method override and lets through a write only with a JSON body, and the two pods resolve one name each. The agent filters and denies the same tools. A daily probe tests both clients of the proxy and the proxy itself, and a daily check tests the DNS rule. The token is a placeholder until the owner puts it in (docs/apps/agent-platform.md, "The finance app").

## v2.186.0 - 2026-11-17

### Added

* Backups: the ClusterRepository agents-finance (prefix kopiur-finance/), which grants the finance trust domain. No policy uses it yet.

### Fixed

* Windows: the write check of windows-rehearsal-check.yml reads the one answer line of the write test, so a warning that wsl.exe writes to stderr before or after it no longer fails a refusal. Two answers still fail.

## v2.185.2 - 2026-11-17

### Fixed

* CI: the summary of the hosts workflow shows a host with no recap line as "did not run" instead of a count of ok 0 and failed 1. ansible-playbook stops before wsl-distro.yml when the play of windows-host.yml ends with a failure, so the distro of a failed Windows host did not run. The drift metrics still count it as failed, and the run still fails.

## v2.185.1 - 2026-11-17

### Fixed

* Windows: a later run of the rehearsal removal runs no task of the temporary account when nothing of it is left to stop (no start task running, no process of the account, no distro in the list in its hive), so it does not load the profile again. When the profile stays loaded, the removal stops with a message that says what stays and to run it again once the profile unloads, at the latest after the next restart of the host.

## v2.185.0 - 2026-11-17

### Added

* Agent platform: web search (with a key of its own) and the notes vault (seeded into its state volume, read through its memory search, counted by the restore test) for the finance agent, in git and not applied yet. The steps of the owner are in docs/apps/agent-platform.md, "The finance agent".

## v2.184.0 - 2026-11-17

### Added

* Backups: the session refresher of the finance repository runs (its role and profile in the wsl cluster settings, and its directory listed for Flux). The ClusterRepository comes in the next step.

## v2.183.0 - 2026-11-16

### Removed

* Windows: the rehearsal of the account change on WSL-02 is removed (windows_wsl_rehearsal, state absent). A task of the temporary account, registered like the others (s4u_user, as SYSTEM), stops and unregisters its distro and shuts down its own VM; the play then removes its tasks, its profile, its batch right and its deny rights, the value that hides its tile, the account and its folder. windows-rehearsal-check.yml reads the exit check.

## v2.182.0 - 2026-11-16

### Added

* Agent platform: a scaffold of the finance agent in `agents-finance` (its config, the scripts of the pilot and its network policy; the Deployment at zero replicas and the state volume are in git and not applied), an embedding entry `agents-finance-embeddings` on the model gateway, and a template of its restore test.
* Backups: a Kopia repository of its own for the finance trust domain (prefix `kopiur-finance/`, its own role, certificate, password and refresher), the deny of the bucket policy for that prefix, and the mover policy and backup policy of the finance agent. The refresher, the repository and the policy are applied in later steps, in the order of docs/operations/backups.md.

### Changed

* Agent platform: the sandbox-start step of each agent pod reads the address of its own sandbox gateway from its scripts ConfigMap, not from the script, and logs one line when the gateway stops or starts to answer.

## v2.181.0 - 2026-11-16

### Changed

* Agent platform alerts: `AgentInstructionCheckStale` and `AgentMemoryIndexFailed` count each trust domain with an agent pod, not the pilot only. A trust domain with no agent pod, or one at zero replicas, never fires them.

## v2.180.1 - 2026-11-16

### Fixed

* Windows: the tasks of the rehearsal account register. Microsoft asks an administrator, SYSTEM included, for the password of a different account to register an S4U task for it. With s4u_user, tnwks_win_scheduled_task now makes a new random password on the host, sets it on the account, registers the task with it and drops it; it does so only when the task must be registered, never in check mode, and the password is in no argument, result, diff or failure text. Only for an account whose password nobody needs: the temporary account of the rehearsal. The module refuses, and changes nothing, for a member of Administrators, an account whose Description is not the required s4u_description, the account of its own process, and an account with an interactive, network or remote logon session open. No cmdlet takes the plain password. The probe and the import name a task that fails to log on at its start.

## v2.180.0 - 2026-11-16

### Changed

* Renovate review: the repository variable `RENOVATE_REVIEW_ROUTE` is `platform`, so each review goes to the run receiver of the workflow agents and its answer is one comment on the pull request. The hook of the host gets no review while the variable is `platform`; setting it to `host` again is the way back.

## v2.179.1 - 2026-11-16

### Fixed

* Windows: the tasks of the rehearsal account register. The module tnwks_win_scheduled_task takes s4u_user, which registers an S4U task for another account through the Schedule.Service object with that user, no password and the S4U logon type. Each task of the role windows_wsl_rehearsal and of the rehearsal check that registers, starts or stops a task of the account runs as SYSTEM (become, runas), since the Task Scheduler refuses that registration to the network logon of the SSH session; every other Windows task keeps become off. A failed registration names each exception of the chain with its HResult, in both paths.

## v2.179.0 - 2026-11-16

### Added

* renovate-review.yaml: a second review job that asks the run receiver of the workflow agents for the review (`POST /runs`), with the retry rule of a caller: the key `renovate-review-<pr>-<sha7>-<n>`, a new attempt only after a failed or timed out answer, and 3 attempts at the most for one head over all the runs. It runs only when the repository variable `RENOVATE_REVIEW_ROUTE` is `platform`; until then each review goes to the hook of the host, as before.

## v2.178.0 - 2026-11-16

### Added

* GitHub settings: the environment `run-receiver` (deployments from `main` only) for the token of the run receiver, and the repository variable `RENOVATE_REVIEW_ROUTE` at `host`, which keeps each Renovate review on the hook of the host until the owner changes it to `platform`. The docs of the review describe the two routes, the move and its way back.

## v2.177.1 - 2026-11-16

### Fixed

* Windows: the role windows_wsl_rehearsal makes its temporary account with a description of 48 characters at most, the limit of New-LocalUser in Windows PowerShell 5.1, from the new default wsl_rehearsal_account_description. A warning that wsl.exe or diskpart writes to stderr stays text in the result of the step and no longer stops it.

## v2.177.0 - 2026-11-16

### Added

* Windows: the role windows_wsl_rehearsal, on WSL-02 only (wsl_rehearsal): a temporary standard account with a distro of its own, imported from a pinned Alpine root file system and started from its own S4U tasks with no elevation, and the play windows-rehearsal-check.yml that reads its checks.

## v2.176.0 - 2026-11-16

### Changed

* Windows hosts: each folder of a role under C:\ProgramData lets only SYSTEM and the Administrators make or change files, and Users read (tnwks_win_dir_acl). At each run, each item below takes the owner Administrators and the rules of the folder; the log and address folders keep their own rules. A folder that no role owns, listed in windows_locked_dirs, is for SYSTEM and the Administrators only (readers: none).

## v2.175.1 - 2026-11-16

### Fixed

* Security: the filter of the aggregators and the reader of the API checks now read the router's version line in the form that the syslog source gives (the prefix as the tag, appname, and the identity and version in message). The filter keeps that line out of the security stream routeros, and the job finds it in the Loki of homelab-wsl-02 (its line filter has no space after the prefix). With no line in 3 hours, the job says so in its log.

## v2.175.0 - 2026-11-16

### Changed

* The AWS runs of Prowler have the memory limit of their measured peak and 25 %, and a request of 704 MiB, the highest working set rounded up. The two AWS systems list prowler as a source, so SecurityResultLate fires when a run has no result for 36 hours.

## v2.174.0 - 2026-11-16

### Changed

* The two AWS runs of Prowler run at 03:17 and 05:17 UTC again, after their run of one day.

## v2.173.0 - 2026-11-16

### Added

* github: a main ruleset on the agent image repository

## v2.172.0 - 2026-11-16

### Added

* Security: the router logs its RouterOS version each hour to the host of homelab-wsl-02 too (a scheduler and a logging rule in Terraform, the syslog relay on WSL-02 and its distro), and the API checks read the version lines of the last 3 hours from that site's Loki. The lines must agree, and agree with the Prometheus of homelab-wsl when it answers; with no line, that Prometheus decides as before. The aggregators keep the line out of the security stream routeros.

## v2.171.3 - 2026-11-16

### Fixed

* logs: the platform log agent keeps its read positions on the node (/var/lib/vector-agent-platform), as the audit reader does, so a restart no longer sends each line of the pods it selects to Loki again with a new time

## v2.171.2 - 2026-11-16

### Fixed

* alerts: Alertmanager drops KubeJobFailed for the run jobs and the four CronJobs of the agent platform, which have alerts of their own that resolve with their cause, so an old failed Job no longer keeps a group open in the alert channel; any other failed Job still reaches it, and a test checks each route

## v2.171.1 - 2026-11-16

### Fixed

* Windows hosts: the scheduled task module reads a task with the run level LeastPrivilege as unchanged. Export-ScheduledTask leaves out the RunLevel element for that default, so each check and the nightly drift run reported such a task as changed, and each apply registered it again.

## v2.171.0 - 2026-11-16

### Changed

* The two AWS runs of Prowler run once today at 08:47 and 09:47 UTC, ahead of the night. A second change sets them back to 03:17 and 05:17 UTC.

## v2.170.1 - 2026-11-16

### Fixed

* ci: a manifest change no longer waits 10 minutes for the observability checks. promtool on the generated rule tests and the PowerShell tests are their own jobs, which run only when observability/, security/ or the host check roles changed, and the rule tests run in shards, one promtool per core

## v2.170.0 - 2026-11-16

### Changed

* agents-workflows: the CronJob workflow-providers runs the providers command of the image of the workflow agents, which its CI tests, in place of the providers script and the GitHub App token script of this repo. The steps, the log lines and the exit codes stay the same.

## v2.169.4 - 2026-11-16

### Fixed

* Alerting: Telegram answered ENTITIES_TOO_LONG for the messages of SecurityResultLate, two alerts each with a graph link of about 5100 characters (its whole expression). The Telegram message now has one graph link, on the first alert, up to 1500 characters in a message of one alert and 1000 in a message of more; the text and the other links of the first three alerts of a group only, and the labels of the next five; at most 1700 characters of link text. The bounds are chosen from a measured delivered message (a graph link of 1444 characters, 1643 of link text) and the measured refused one (10460), not from a documented limit. A test renders each alert of the repository through the template with amtool and checks the tags, the number of entities, the text length, the size and the links.

## v2.169.3 - 2026-11-16

### Fixed

* Each Prowler run of AWS runs one process of Prowler for each service of the scan list, and joins their records into one OCSF file. A process holds one service at a time, and a run in which a part fails leaves no file.

## v2.169.2 - 2026-11-16

### Security

* agents-workflows: the admission policy of the run jobs pins the run job container to the entrypoint of the image with the one argument run-job (no command, lifecycle hook or probe), and a new policy, workflow-run-job-updates, lets an update of a run Job by the run receiver change only its two annotations (the kept answer and the check of the Sandbox record). Both bind the account of the receiver only. A test harness (admission/tests/run.sh) runs them on a throwaway API server in containers.

## v2.169.1 - 2026-11-16

### Fixed

* terraform: the bucket policy of an exclusive backup prefix did not apply, because S3 refuses the s3:prefix condition on s3:ListBucketMultipartUploads. The list deny now names ListBucket and ListBucketVersions only.

## v2.169.0 - 2026-11-16

### Changed

* Windows hosts: a per-host switch, wsl_start_unelevated (on for WSL-02 first), runs each task that starts a process in the WSL distro with no elevation, and moves the steps that need admin rights to tasks that start none: tnwks-wsl-service (SYSTEM) for the WSL service, and tnwks-lan-bridge-sync as SYSTEM with the VM address from the new task tnwks-lan-bridge-ip. The compaction no longer starts the distro itself under the switch. No elevated client creates the WSL VM or starts a process in a distro, and each directory that a task with no elevation writes is made by a new module, tnwks_win_dir_acl, which lets the account make and change files there but not change the directory itself. The services of a distro take the change at its next start, a step by hand with a runbook that stops WSL with wsl --shutdown (docs/wsl/windows-ansible.md). A second switch, wsl_start_unelevated_done, goes on after that step and its read, and only then can the write test of the distro fail its play.

## v2.168.2 - 2026-11-16

### Security

* terraform: a backup prefix of rolesanywhere_backups_prefixed can be exclusive: a bucket policy denies its objects, and a list under it, to every principal but its own role and the break-glass roles. kopiur-agents/ is exclusive, with the AdministratorAccess role of IAM Identity Center as break-glass.

## v2.168.1 - 2026-11-16

### Fixed

* Security: the relay of the WSL distro host check also refuses a document that has more than one name (a hard link). The task that resets the files of C:\ProgramData\tnwks-host-check accepts SYSTEM as their owner, so the relay log that SYSTEM makes there is no change for a check run. The security README describes the inbox rules, the relay log and the checks of the relay as they are.

## v2.168.0 - 2026-11-16

### Added

* Security: the host check of each WSL distro gives the errno of each try of the write test as a series, and the host check of each Windows host counts the files of the write test in its textfile directory.

### Changed

* Security: the host check of each WSL distro writes a JSON document into an inbox, C:\ProgramData\tnwks-host-check-distro, where the account of the distro may make and change files but not change the folder itself. A relay on its Windows host, a SYSTEM task each hour, checks the document and writes the .prom file of the distro; a document that does not fit, a repeated series, or a link on the way gives no result. The relay logs each run to C:\ProgramData\tnwks-host-check\relay.log. The distro account loses its right on the textfile directory, and its directory wsl-distro goes.

## v2.167.0 - 2026-11-15

### Added

* Security: the host checks of each Windows host and each WSL distro add the check updates-held, for a held package or a hidden update that is not optional, older than 14 days (severity high). In a distro, each package has one clock whether it waits, is held or is phased; an upgrade that a run sees first is dated by the Date of its package list (by its Label), read before the simulations of the last run with a result, or counts as old with no such Date; and only a run with a result writes the state file.

### Fixed

* Security: the host check of each WSL distro reads the answer of the write test of root only from a regular file, and opens it without waiting, so a FIFO in its place is "not done" at once and does not hold the run.

## v2.166.1 - 2026-11-15

### Fixed

* security: a Trivy scan reads the layers of an image one at a time (scan.parallel 1), not 5, so a scan container of a large image no longer reaches its limit of 512 MiB and is killed with no report; the limit stays

## v2.166.0 - 2026-11-15

### Changed

* Security: the check of the Defender signatures of each Windows host and the MFA check of Terraform Cloud have the severity high, so a new fail of either starts SecurityFindingNew, as Design Doc 0004, section 5.8, selects.

## v2.165.0 - 2026-11-15

### Added

* Security: the host check of each WSL distro first tries to make a file in the textfile directory of its host, as root (a step of the service before the check) and as the distro user. Only a refusal by Windows for root is the pass; a write, a missing directory or no answer of root is the failed query textfile-write. The role runs the same tries and prints each answer with its uid.

## v2.164.0 - 2026-11-15

### Added

* Security: homelab-wsl-02 scrapes the security series of both Windows hosts itself (job windows-exporter-security), in place of the federation of the host check through homelab-wsl. homelab-wsl keeps them with the label standby, and SecurityFindingNew fires there for them only while homelab-wsl-02 does not scrape their host. SecurityHostUnreachable fires when homelab-wsl-02 can not scrape the host of site 1 for 5 minutes, and two inhibit rules hold the late results behind that site (behind_site in systems.yaml) and PeerSiteUnreachable of that site while it fires.

## v2.163.0 - 2026-11-15

### Added

* DESKTOP-HKE05KI's windows_exporter and smartctl_exporter also admit WSL-02's address, so homelab-wsl-02's Prometheus can scrape the security series of the site 1 host over the LAN (`windows_monitoring_remote_extra`, as WSL-02 has for the other way).

## v2.162.1 - 2026-11-15

### Security

* kopiur: the shared repository no longer allows agents-pilot, now that the old pilot policy agent-state is gone; its snapshots stay there under their kopia identity.

## v2.162.0 - 2026-11-15

### Changed

* security: the memory limit of the Trivy Operator raises from 512 to 640 MiB on both sites, with GOMEMLIMIT at 576 MiB (90%): on homelab-wsl the first cycle peaked at 473 MiB, under 8% below the old limit

## v2.161.3 - 2026-11-15

### Fixed

* kopiur: the shared repository allows agents-pilot again until the old pilot policy agent-state finishes its delete, which the admission webhook refuses without the grant. The docs give the two-step order for moving a trust domain to another repository.

## v2.161.2 - 2026-11-15

### Fixed

* security: a Trivy report of an image holds its critical vulnerabilities only, by an ignore policy in the scan containers, so the report of an image with many fixed vulnerabilities fits in one object of the API server ("etcdserver: request is too large"); trivy.severity keeps every severity, which the compliance report reads

## v2.161.1 - 2026-11-15

### Fixed

* security: Trivy Operator keeps the scan cache of each scan in memory, so the containers of one scan job no longer stop on the lock of the file cache ("cache may be in use by another process: timeout") and leave their images with no report; the vulnerability databases stay on the volume. The containers of a job now scan at the same time: the memory of a job is an estimate until the first cycle measures it

## v2.161.0 - 2026-11-15

### Changed

* security: homelab-wsl lists `trivy` in its sources, after its first result, so SecurityResultLate watches Trivy Operator there. A pending source in the test of the sources of a cluster now has a last day and must be reached, and the federation test covers the series of `trivy` from homelab-wsl and the accepted findings of `trivy` on homelab-wsl

## v2.160.0 - 2026-11-15

### Added

* security: Trivy Operator on homelab-wsl, with the values of homelab-wsl-02. `peer-federate` of homelab-wsl-02 brings the two series of the registry of `trivy` from homelab-wsl, and `peer-federate` of homelab-wsl brings the accepted findings of `trivy` from homelab-wsl-02. The limits are those of homelab-wsl-02, where a full cycle peaked at 316 MiB of 512 MiB for the operator and 97 MiB for the containers of one scan job. homelab-wsl lists `trivy` in its sources in a later pull request, after its first result, so the late alert does not fire before the first scans; a test names the pending pair until then

## v2.159.2 - 2026-11-15

### Fixed

* agents-workflows: a Sandbox record that the sandbox gateway left after a delete is now seen and removed. The run receiver can list Sandbox records, to check the record of each run after its end; the sweep runs as its own service account workflow-sweep with list and delete on Sandbox records, an admission policy (workflow-sweep-deletes) holds its deletes to run records, and its own network policy adds the API server. The run jobs keep no token and the gateway as their only egress.

## v2.159.1 - 2026-11-15

### Security

* kopiur: the agent trust domains back up to a Kopia repository of their own (ClusterRepository agents, prefix kopiur-agents/, its own password and role). The pilot agent policy is now agent-state-v2; the snapshots of agent-state stay in the shared repository, which no longer allows agents-pilot. New alert AgentBackupRepositoryNotReady for a repository of the trust domains that never gets Ready.

## v2.159.0 - 2026-11-15

### Added

* kopiur: the credentials of a Kopia repository for the agent trust domains (homelab-wsl): its own password, and a refresher that turns its own certificate into an S3 session for the role kept to kopiur-agents/

## v2.158.1 - 2026-11-15

### Fixed

* ci: the observability contracts job has 25 minutes instead of 15, since its rule tests alone take 6 to 9 minutes on a hosted runner and the job reached 11.5 minutes on main

## v2.158.0 - 2026-11-15

### Added

* terraform: a Roles Anywhere backup role and profile kept to one prefix of the backup bucket (list, read, write and delete under it, nothing else), with its own certificate CN, for the Kopia repository of the agent trust domains

## v2.157.0 - 2026-11-15

### Added

* security: the alert SecurityImagesScannedByTag (warning) fires when containers on a cluster with Trivy Operator have matched their vulnerability report by reference only, not by digest, for 7 days: the tag moved after the node pulled it, so the report is of another image; restart the workload or pin a digest. An entry of the accepted list with the check images-scanned-by-tag mutes it for the cluster

### Fixed

* security: a vulnerability report of Trivy Operator counts for a running container by its digest, or, when no report has that digest, by the same namespace, container and image reference, so an image whose pod and report show different digests of the same reference (a tag that moved, or an image index and a platform image) is not counted as not scanned, and its critical vulnerabilities count once in image-vulnerabilities (when two reports have the reference and neither has the running digest, the one with the most critical vulnerabilities); the security board shows the number of containers matched by reference only

## v2.156.3 - 2026-11-15

### Fixed

* agents: the pilot config names no Brave plugin path any more (the plugin is a verified official install from the agent image since 0.4.0), so the gateway logs no load path warning at each start

## v2.156.2 - 2026-11-15

### Security

* agents: the pilot agent loads only the plugins that it names in plugins.allow (brave, diagnostics-otel, memory-core, openshell, telegram), not each plugin that the product finds in its state directory or bundles

## v2.156.1 - 2026-11-15

### Fixed

* agents: web search works in the sandboxed sessions of the pilot agent: the Brave plugin is a verified official install from the agent image 0.4.0 (the official-plugins init container, which installs it again at each start and stops the start on a plugin in the state that the image did not put there), not a plugin from plugins.load.paths, which a sandboxed session refuses

## v2.156.0 - 2026-11-15

### Added

* security: the router lists `api-check` in its sources, now that the API checks have its first result on homelab-wsl-02, so `SecurityResultLate` watches it

## v2.155.0 - 2026-11-15

### Added

* github: the client ID of the release App on the agent image repository, for its releases

## v2.154.2 - 2026-11-15

### Fixed

* security: each Prowler run of an AWS account has a memory limit of 1536 MiB and a request of 1024 MiB (was 768 and 512): the first runs ended OOMKilled, as Prowler takes about 725 MiB before it keeps any resource; the README of prowler has the measure and the ways to bring the peak down, and the budget of homelab-wsl-02 counts the new limit

## v2.154.1 - 2026-11-15

### Fixed

* agents: the sandbox probe removes a Sandbox record of its own name that an earlier run left before it creates its sandbox, and fails a run whose own record is still there at its end, so a delete that the sandbox gateway answered and did not do no longer blocks the next runs

## v2.154.0 - 2026-11-15

### Added

* agents: alerts when the memory index step of the pilot fails at a start, and when the admission rule refuses a ConfigMap write of a backup mover in a trust domain (homelab-wsl keeps that one API server counter)

## v2.153.0 - 2026-11-15

### Added

* agents: web search for the pilot agent through the Brave Search API, with its key for the agent container only

## v2.152.0 - 2026-11-15

### Changed

* github: secret scanning and push protection on the agent image repository

## v2.151.0 - 2026-11-15

### Changed

* github: the agent image repository is public

## v2.150.2 - 2026-11-15

### Fixed

* agents: the pilot pod rebuilds a stale memory index at each start (an init container after doctor), so a new embedding model needs no step by hand

## v2.150.1 - 2026-11-14

### Security

* privacy: bin/privacy-check reads the denylist in two forms, the plain list and one opaque line that `bin/privacy-check --encode` makes from it; a value that reads as neither stops the check. Hits print as "hit: <where>" with every term in it replaced by "[term]", case is ignored outside ASCII too whatever the locale, paths print as they are rather than as escapes, and a failed search stops the check. task privacy:sync gives a repository the opaque line once its bin/privacy-check reads it and its comment check pins the fixed shared workflow, and the plain list until then, and says which. Tests in .github/scripts/test_privacy_terms.py.

## v2.150.0 - 2026-11-14

### Removed

* tools: the runner scale set of the agent image repo, which goes public; its jobs run on GitHub-hosted runners

## v2.149.0 - 2026-11-14

### Added

* ai: memory search for the pilot agent. Its embeddings come from a new entry of the model gateway, agents-pilot-embeddings, on the Ollama of the host (docs/apps/agent-platform.md).

## v2.148.1 - 2026-11-14

### Security

* agents: an admission rule refuses each ConfigMap write of a backup mover in the namespaces of the agent trust domains

## v2.148.0 - 2026-11-14

### Changed

* github: Owner label is a required check of the main ruleset, from GitHub Actions. An impact/high or impact/critical PR merges only with the label of the owner (docs/operations/renovate.md).

## v2.147.0 - 2026-11-14

### Added

* ci: the Owner label check (.github/scripts/owner-label-check.py, with its tests). It passes an impact/high PR only when the owner added owner/review, and an impact/critical PR only when the owner added owner/approved, after the push of the head commit. A label from a bot, a GitHub App or a workflow token fails it. Every other PR passes, so the automerge tiers keep merging. The workflow that runs it and the ruleset entry come from the owner (docs/operations/renovate.md).

## v2.146.0 - 2026-11-14

### Added

* ai: a runbook of the agent platform (docs/apps/agent-platform-runbook.md), with a section for each of its alerts, the restore of the state of an agent, the fast stop of the workflow agent, the move to Bedrock by hand and the steps at a limit of the Claude plan. Each rule of the platform links to its own section, and a test checks that each runbook link into this repository has its section

## v2.145.0 - 2026-11-14

### Added

* security: both Windows hosts and both WSL distros list `host-check` in their sources, now that each has its first result on homelab-wsl-02, so `SecurityResultLate` watches them

## v2.144.0 - 2026-11-14

### Added

* security: the host check in each WSL distro (Ansible role `wsl_host_check`, source `host-check`). A systemd timer simulates an upgrade with apt-get every 6 hours and writes the result into a directory of the Windows host for its windows_exporter. An upgrade fails the check after it has waited 14 days. The result carries the severity high. The age of the lists is the oldest list of the updates and the security fixes of the distribution, and over 72 hours, a query that fails or a state file that can not be read gives no result. A follow-up adds `host-check` to the sources of the distros after their first result

## v2.143.0 - 2026-11-14

### Changed

* ai: the model gateway sends each request to the model bridge one time (`num_retries: 0` on the three bridge entries). At the usage limit of the plan a call no longer reaches the bridge 3 times from the gateway alone. The Bedrock entry and every other entry keep the router default of 2 retries

## v2.142.0 - 2026-11-14

### Added

* security: the host check on each Windows host (Ansible role `windows_host_check`, source `host-check`). A SYSTEM scheduled task reads Defender, the updates and the firewall every 6 hours and writes the result for the textfile collector of windows_exporter. Each result carries a severity, so a failed check of Defender, the firewall or the updates alerts. Only SYSTEM and the Administrators may change the directory of the script. A query that fails gives no result. A follow-up adds `host-check` to the sources of the hosts after their first result

## v2.141.0 - 2026-11-14

### Added

* tools: runner scale sets for the checks of two private app repos, with no Docker; their image jobs stay on GitHub-hosted runners

## v2.140.0 - 2026-11-14

### Added

* security: the API checks on homelab-wsl-02 (`kubernetes/apps/security/api-checks`, source `api-check`). A CronJob tests each day whether the router runs the newest stable release of RouterOS, from the series of mktxp and the release feed of the vendor (safeguard 12.1), and, once the owner adds its token, whether the Terraform Cloud organization requires two factors (safeguards 6.3 and 6.5). It keeps each newest release it sees, so a router two releases behind fails too. It writes the result file that the converter reads. A test that does not run has no entry. A follow-up adds `api-check` to the sources of the router after its first result

## v2.139.0 - 2026-11-14

### Added

* security: `peer-federate` of homelab-wsl-02 brings the two series of the registry of the host check (`host-check`) from homelab-wsl, which scrapes the textfile collector of both Windows hosts. Nothing writes the series yet: the Ansible roles of the host check start it

## v2.138.1 - 2026-11-14

### Fixed

* monitoring: the platform log agent (vector-agent-platform) sends to the aggregator over plain HTTP. Its sink address had no scheme, which Vector 0.61 reads as https, so no line of the agent platform reached Loki. A container test now sends one event through each vector sink to the aggregator.

## v2.138.0 - 2026-11-14

### Added

* ai: a daily check that Tempo has a completed heartbeat run of each personal agent in the last 26 hours (CronJob agent-heartbeat-check, which asks up to 3 times and retries a failed pod once), with the alerts AgentHeartbeatMissing and AgentHeartbeatCheckStale.

## v2.137.0 - 2026-11-14

### Added

* monitoring: the logs of some long-running pods of the agent platform go to Loki, on an allowlist (the pod label logs.home.arpa/ship=true): the run receiver and the sandbox gateway of the workflows trust domain, its sandbox supervisors, the backup, sandbox-start and init containers of the pilot agent pod, and the model bridge. A new Vector DaemonSet on homelab-wsl only (vector-agent-platform) reads them, drops command text and query strings from the gateway lines, keeps only known line types of the supervisors, and serves the gauges of the new alerts AgentInstructionFileChanged and ModelBridgeRateLimited. The new alert AgentInstructionCheckStale reads the result gauge of the aggregator. The shared Vector agent and the aggregator do not change.

## v2.136.0 - 2026-11-14

### Added

* ai: alerts of the agent platform: a workload that is not ready, a failed last run and a stale run of its CronJobs (the providers job after 35 minutes, the daily checks after 26 hours), a drift of the GitHub App of the reviewer, an old run sandbox, a failed run, and a failed daily archive. The Vector aggregator serves the time of the newest result line of the platform, for the drift alert.

## v2.135.0 - 2026-11-14

### Added

* security: the Prowler run for Cloudflare (`kubernetes/apps/security/prowler`), each day with the scan list of the registry, and a network policy for `api.cloudflare.com`. It does not run until the owner adds its read-only token as a SOPS Secret and names it in the app; the README has the steps and the least scope of the token. The new alert `SecurityTokenExpiring` fires 30 days before the end date that the owner writes to the Lease of a token. GitHub has no Prowler run, as it would fail each day on a personal account: the API checks take its lines of the registry

## v2.134.0 - 2026-11-14

### Added

* security: the Prowler runs of the AWS accounts start on homelab-wsl-02: `prowler-aws-prod` each day at 03:17 UTC and `prowler-aws-management` at 05:17 UTC, now that the AWS session Secret exists

## v2.133.0 - 2026-11-14

### Added

* security: the result series of Trivy Operator (`observability/rules.py`): `homelab_security_check_result` for each control of `k8s-cis-1.23` that has an input on these clusters and for `image-vulnerabilities` (a fail stays, and a pass is no evidence, while a running image has had no report for an hour; the new alert `SecurityImagesNotScanned` fires after 36 hours of that), and the time of the last result from the compliance report through kube-state-metrics. An accepted finding of `trivy` gives 0 until its end, also while its series is gone for less than a day. The new alert `SecurityFindingNew` fires when a result of high or critical severity is above 0 and above each value of the day before, or was 0 one hour before, or had no value in the day before, with one value for each check from a recording rule. homelab-wsl-02 lists `stream` and `trivy` in its sources, and a test fails if a cluster runs an app of a source that its line does not list, so the late alert watches it

## v2.132.0 - 2026-11-14

### Added

* security: the converter mounts the volume `security-results` read-only at `/results`, so it reads the OCSF files of the Prowler runs and the result file of the API checks

## v2.131.0 - 2026-11-14

### Added

* security: Prowler 5.49.0 for the AWS accounts (`kubernetes/apps/security/prowler`): a CronJob each day for `aws-prod` and one for `aws-management`, with the scan list of the registry, an OCSF file for the converter on the new claim `security-results`, the accepted findings (now the JSON key `accepted.json` of the Secret) as its only mutelist, nothing sent to the vendor, and a network policy that names the AWS API domains. No run starts until the owner names the CronJobs in the app, after the AWS sessions exist. `security/generate.py` writes the scan list of each provider for the app

## v2.130.0 - 2026-11-14

### Added

* security: the converter sends each finding that fails in the newest file of a system as one line of the security stream `findings` in the Loki of homelab-wsl-02 (labels `security_stream`, `source`, `system`; kept 90 days by the rule of the security streams). A line is a part of the OCSF record: what failed, where and why, with no data, tag or label of a resource. The lines of a file go one time, wait for the next cycle when Loki does not answer, and `homelab_security_converter_findings_pending` shows them. A batch is 1 MiB at most in bytes on the wire, a 413 waits too, and one stream does not stop the others

## v2.129.1 - 2026-11-14

### Fixed

* security: Trivy Operator gets 512 MiB on each cluster (it was 256 MiB, too low for a burst of scans), a request of 256 MiB, and `GOMEMLIMIT` at 460 MiB. On homelab-wsl-02 the operator pod restarts once

## v2.129.0 - 2026-11-14

### Added

* security: the converter reads the files of results: the newest OCSF file of each system that Prowler writes, and the result file of the API checks. It serves `homelab_security_check_result` (the number of resources that fail, with a lowercase `severity` label; a finding that the accepted list muted does not fail) and the time of the newest whole file of each system. A file that does not fit gives no result and no time for its system, so the late alert fires, and `homelab_security_converter_files_skipped` counts it. It reads an OCSF file one record at a time, opens only a regular file, and takes only a check ID as a check. The ServiceMonitor drops the labels of the pod, so a restart makes no new series. Nothing is read before the converter mounts the volume `security-results`

## v2.128.0 - 2026-11-14

### Added

* security: the list of accepted findings is one JSON array (the key `accepted.json` of the Secret `security-accepted-findings`), with five string fields for each entry. The converter reads it with `json.loads` (the Secret mounted read-only and optional) and serves `homelab_security_accepted_until_timestamp_seconds` with `source`, `check` and `system` for each entry, so the board counts the accepted findings and `SecurityAcceptedFindingExpired` can fire. No reason leaves the Secret. A list that is not one JSON array, or has a key two times in an object, gives no entry, and `homelab_security_converter_accepted_list_read` says so; so does a list left in the old key `accepted.yaml`, and a fault of the reader itself. An entry with a missing or extra key, a value that is not a string, no reason, or an end date not of the form `YYYY-MM-DD` is skipped. The alerts `SecurityAcceptedListUnread` (no entry for 30 minutes) and `SecurityAcceptedEntriesSkipped` (skipped entries for one hour) make both loud. Its parts now run in one loop, and a fault of one part does not stop the others. `security/generate.py` writes the names of the registry for the converter (`registry.json`)

## v2.127.0 - 2026-11-14

### Added

* security: Trivy Operator 0.36.1 on homelab-wsl-02 (`kubernetes/apps/security/trivy-operator`), the source `trivy` of the security registry. It checks the workloads, the roles, the control plane pods, the controls of the CIS Kubernetes Benchmark that read the API server, and the images for vulnerabilities with a fix, one scan job at a time. The node collector is off, the images are pinned by digest, the version check and the usage data of Trivy are off, and a network policy names each registry host that a scan job can reach. The operator creates its scan jobs and reads their logs in `security` only, and a scan job runs as `nobody`

## v2.126.0 - 2026-11-14

### Added

* security: the copy of the AWS trail on homelab-wsl-02 (`kubernetes/apps/security/trail-copy`). A CronJob copies the new files of the trail bucket to a volume every 15 minutes, keeps them 7 days, and sends each event to the Loki of the site as the stream `security_stream="cloudtrail"`, one stream for each account and Region. A file with a line that Loki refuses fails the run and is not sent again. A quiet Region is listed each 4 hours. The AWS systems of the registry have `stream` as a source, so the rule for a late result watches the trail

## v2.125.0 - 2026-11-14

### Changed

* privacy: task privacy:edit copies the denylist to the PRIVACY_TERMS secret when the editor closes, if the list changed and still decrypts; an aborted or failed edit copies nothing. The copy goes to every repository of the account that already has the secret, found through the API (bin/privacy-terms), and names only public ones. task privacy:sync stays as the retry

## v2.124.0 - 2026-11-14

### Added

* security: homelab-wsl-02 starts the AWS credential refresher (`kubernetes/apps/security/aws-credentials`). The cluster settings get the two Roles Anywhere profiles, the two client certificates come as SOPS Secrets that end on 2027-11-14, and the cluster lists its Flux Kustomization. Every 30 minutes it writes a 4-hour read-only session to `aws-security-audit-sts` and `aws-security-trail-sts`, and the end of each certificate to its Lease for `SecurityCertificateExpiring`

## v2.123.0 - 2026-11-14

### Added

* ci: a nightly drift check for the Terraform roots with drift = true in domains.toml, on for the github root. terraform-drift.yaml plans main in the root's lock group on homelab-wsl's ops runner and never applies; the result goes to a new Pushgateway on homelab-wsl, and TerraformDrift (changes, or a failed check) and TerraformDriftCheckStale (no result for 30h) alert on it. The conformance check now covers every workflow that runs actions/terraform: the mask first, the root's lock, plan mode for a drift job

## v2.122.0 - 2026-11-14

### Added

* aws: the AWS parts of the security platform in `modules/aws/security`, called by the prod root. An organization trail in the management account (management events, every Region, log file validation) writes to a private bucket in the prod account that deletes each object after 90 days. Two read-only IAM Roles Anywhere roles, each for one certificate CN: an audit role for Prowler and a reader of the trail bucket. An audit role in the management account that the audit role of the prod account can assume. Each plan warns while an account lists another trail

## v2.121.0 - 2026-11-14

### Changed

* talos: the patch lists of homelab-wsl name a multi-document set (patches/cluster.yaml, patches/controlplane.yaml, patches/audit-policy-multidoc.yaml), so a rebuild with the pinned Talos gets the settings of the live node's patch files, the audit policy among them. The live node keeps its v1alpha1 files for its patch commands until its conversion, and a test holds the two forms equal, field by field. The defaults that Talos 1.14 generates and an older release did not are not compared. CI now has the pinned talosctl build and validate each Docker cluster's machine configs from its lists, with throwaway secrets. A merge applies nothing to a running node

## v2.120.0 - 2026-11-14

### Removed

* terraform: the GitHub root's import blocks for the Flux webhook and the openclaw-hooks variables (imports.tf), now that an apply has imported them; docs/operations/github.md keeps how to adopt something that already exists on GitHub

### Security

* ci: sops-mask.py refuses a value shorter than 6 characters, naming its file and key path. The runner masks a value of any length, also inside longer words, so a short value showed as *** in ordinary text and could be read off. The GitHub root keeps such a value in unmasked.sops.yaml, which CI decrypts but does not mask, and the docs state what the mask guarantees

## v2.119.0 - 2026-11-14

### Added

* monitoring: homelab-wsl lists the audit reader, so the audit log of its API server is the security stream `k8s-audit` of that cluster in the Loki of homelab-wsl-02. The registry of the security platform lists the stream as a source of homelab-wsl

### Changed

* monitoring: the memory limit of the audit reader is 192 MiB on each cluster that lists it, up from 128 MiB. On homelab-wsl-02 the reader used 115.6 MiB at its peak while it sent its first backlog of about 880,000 lines

## v2.118.0 - 2026-11-14

### Added

* terraform/github: the root adopts the Flux webhook of tnwks-ops and the three variables of its openclaw-hooks environment. The webhook URL and the variable values are in the new tnwks-ops.sops.yaml, which CI masks. Terraform never updates a webhook, so it cannot remove the secret of the Flux receiver. The secrets that only the owner holds stay by hand

## v2.117.0 - 2026-11-14

### Added

* terraform: every repository of the GitHub root has archive_on_destroy, so a destroy that ever gets past prevent_destroy archives instead of deleting; the apply is one in-place update per repository that resends its current settings

## v2.116.0 - 2026-11-14

### Removed

* terraform: the GitHub root's import blocks (imports.tf), now that the first apply has imported all 36 objects; docs/operations/github.md keeps how to adopt something that already exists on GitHub

## v2.115.2 - 2026-11-14

### Security

* agents: the pilot denies the tool github_publish, so its outward paths rest on a written rule

## v2.115.1 - 2026-11-13

### Fixed

* talos: the control plane patch list of each WSL cluster (controlplane_patches in talos_clusters) names its audit policy. A rebuild of homelab-wsl-02 gets the policy from the list. The patches of homelab-wsl are in the v1alpha1 form, which the pinned Talos refuses at create, so its rebuild needs them in the multi-document form first. A change under talos/ now runs CI: each file parses, and each YAML file of a cluster is in its lists or is no patch. A merge applies nothing to a running node; talos/README.md says how the policy reaches one

## v2.115.0 - 2026-11-13

### Added

* monitoring: the audit reader, a small Vector DaemonSet on the control plane node that reads the audit log of the API server and sends it to the audit source of the aggregator, with acknowledgements. It runs as user 65534 with no capability and no ServiceAccount token, and it keeps its positions on the node. homelab-wsl-02 lists it, and the registry of the security platform lists the stream as a source of that cluster. `kubernetes/apps/monitoring/vector/README.md` has its properties

## v2.114.1 - 2026-11-13

### Fixed

* terraform: the GitHub root's CI switch is TERRAFORM_GITHUB_ENABLED (GitHub refuses to set a variable named GITHUB_*, so the root could not be turned on), and task github:enable reads it through the API instead of gh variable get, which older gh releases lack

## v2.114.0 - 2026-11-13

### Added

* terraform: the GitHub root's token (secrets.sops.yaml, encrypted for the CI age key) and the import blocks that adopt its tnwks-github-prod workspace in aws-init, both from task github:bootstrap

## v2.113.0 - 2026-11-13

### Added

* terraform: the GitHub account as a root, environments/prod/github with modules/github, importing every repository of the account (private ones whose names are not public under neutral keys, with the names in SOPS) and the main ruleset, environments, Actions settings and variables of tnwks-ops as they are; its Terraform Cloud workspace, a CI entry off until GITHUB_TERRAFORM_ENABLED is true that masks the values of the root's SOPS files in its public log (mask_sops in .github/ci/domains.toml), task github:bootstrap and task github:enable to bring it up, break-glass tasks and docs/operations/github.md

## v2.112.0 - 2026-11-13

### Added

* talos: the audit policy of the API server of homelab-wsl (`talos/wsl/patches/audit-policy.yaml`), with the rules of homelab-wsl-02 in the v1alpha1 form of that cluster (`cluster.apiServer.auditPolicy`). The file has the command that applies it to the running cluster and the way back; a merge applies nothing

## v2.111.0 - 2026-11-13

### Added

* talos: the audit policy of the API server of homelab-wsl-02 (`talos/wsl-02/patches/audit-policy.yaml`, a `KubeAuditPolicyConfig`). It keeps each request on a Secret by each user, each command in a pod, each write, and each read of a person, and it does not keep the heartbeat writes and the reads of the components. The file has the command that applies it to the running cluster and the way back; a merge applies nothing. `security/tests/test_audit_policy.py` gives sample requests to the rules

## v2.110.0 - 2026-11-13

### Added

* monitoring: the alert `SecurityAuditLinesRefused` (group `homelab.security`), with promtool fixtures: the sink of a Vector aggregator that writes the audit log to the Loki of the security streams dropped a request that Loki refused. It reads `vector_component_errors_total` of that sink only, and it fires at the first refused request, though the counter starts at 1

## v2.109.0 - 2026-11-13

### Added

* monitoring: the Vector aggregator of each cluster writes two security streams to the Loki of homelab-wsl-02: the audit log of the API server, from a source of its own on port 6010 (the ClusterIP Service `vector-aggregator-audit`), at the time of each event and with acknowledgements, and a second copy of the router log. The stream labels are `security_stream` and `k8s_cluster`. homelab-wsl writes through the TLS path (`SECURITY_LOKI_ENDPOINT`). `kubernetes/apps/monitoring/vector/README.md` has the contract. The registry of the security platform lists the stream as a source of `sce-rtr01`, so the rule for a late result watches the router log

## v2.108.0 - 2026-11-13

### Added

* monitoring: a path on which homelab-wsl writes to the Loki of homelab-wsl-02, which keeps the security streams: the global Service `security-loki`, a TLS proxy on homelab-wsl-02 that asks for a client certificate of the shared CA and passes only the push of log lines, the two certificates, and a network policy for the proxy. `kubernetes/apps/monitoring/security-loki-proxy/README.md` has the address for a sender on each site

## v2.107.0 - 2026-11-13

### Added

* monitoring: the Vector aggregator of each cluster serves its own metrics (an `internal_metrics` source and a `prometheus_exporter` sink on port 9598 of the pod), and a PodMonitor scrapes them. The port is not on the LoadBalancer Service. `kubernetes/apps/monitoring/vector/README.md` lists the ports and the series

## v2.106.0 - 2026-11-13

### Added

* security: the AWS sessions of the security platform in `kubernetes/apps/security/aws-credentials`. A CronJob in the form of the backups turns each of two IAM Roles Anywhere certificates into a 4-hour session, in `aws-security-audit-sts` for Prowler and `aws-security-trail-sts` for the trail copy job, with a network policy for what it reaches. `SecurityCertificateExpiring` fires 30 days before a certificate ends, from the end time that the refresher writes to a Lease, with promtool fixtures. homelab-wsl-02 lists the app once the owner has made the two certificates

## v2.105.0 - 2026-11-13

### Added

* security: the converter runs on homelab-wsl-02, in the namespace `security` on the security node: the HelmRelease `security-converter` with a memory limit of 128 MiB and no service account token, a ServiceMonitor that the Prometheus of the site scrapes each minute, and a network policy that lets it reach the Loki gateway of the site and DNS, and lets Prometheus reach its metrics port

## v2.104.0 - 2026-11-13

### Added

* security: the converter of the security platform (`security/converter`), with its stream source. Each 5 minutes it asks Loki for the time of the newest line of each security stream, for each system, and serves it as the time of the last result. With no line, an error or no answer it serves no time, so the rule for a late result reads the stream as late. The generator writes the stream map from the registry (`system_label` in `sources.yaml`), and `security/converter/README.md` has the contract of the files of results

## v2.103.0 - 2026-11-13

### Added

* security: the board Security in Grafana, from the board input of the control registry (the state of each safeguard, by system and by source, and the count of accepted findings). The generated rules of the registry run on homelab-wsl-02. `kubernetes/apps/security/accepted-findings` is the place of the encrypted list of accepted findings, with an alert for an end date that has passed, and `security/README.md` has its form

## v2.102.0 - 2026-11-13

### Added

* security: `security/generate.py` makes the rules, the Prowler scan lists, the Wazuh map, the board input and the list of gaps from the control registry, and checks the registry against its schema. The `contracts` job of the observability workflow runs it with `--check`, with the tests of the generator and of the rules. A system with no fresh result gets "no evidence", a safeguard is a pass only when each system with a check for it passes, and one alert rule watches the time of the last result of each source

## v2.101.0 - 2026-11-13

### Added

* security: the control registry of the security platform in `security/registry` (the systems in scope, the sources with their cycles, and the 48 safeguards of CIS Controls v8.1 IG1 with their checks for each type of system), its schema, and the contract of the result series and the control series in `security/README.md`

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
