# The security platform: the control registry

This directory holds the control registry of the security platform, and the
contract that each part of the platform builds on. The registry lists the
systems that the platform checks, the sources of results, and the safeguards
of CIS Controls v8.1 with the checks that test each one.

The registry names controls, systems and checks. It holds no result: a
result is a Prometheus series on the sites, and the board shows it.

| Path | What it is |
| --- | --- |
| `registry/schema.json` | The form of the three registry files |
| `registry/systems.yaml` | One line for each system in scope |
| `registry/sources.yaml` | One line for each source of results, with its cycle |
| `registry/controls.yaml` | One line for each safeguard, with its checks for each type of system |
| `catalog/` | The check list of each tool, at the release that the registry follows |
| `generate.py` | The generator. It checks the registry and writes the outputs below |
| `converter/` | The converter: the service that serves the results of the sources with no metrics of their own. Its README has the stream source and the contract of the files of results |
| `generated/` | The outputs of the generator that no cluster reads directly, and the tests of the rules |
| `tests/` | The tests of the generator |

Four parts are in other directories, by the conventions of the repository:

| Path | What it is |
| --- | --- |
| `kubernetes/apps/monitoring/security-registry/` | The generated rules, and the Flux Kustomization that applies them on homelab-wsl-02 |
| `observability/dashboards/boards/security.py` | The security board. It reads `generated/board.json` |
| `kubernetes/apps/security/accepted-findings/` | The place of the encrypted list of accepted findings |
| `kubernetes/apps/security/converter/` | The converter on homelab-wsl-02, with the copy of its code and its stream map |

## Names

| Name | Meaning |
| --- | --- |
| Safeguard | One item of CIS Controls v8.1, such as 4.1 |
| System | One thing that the platform checks: an AWS account, a cluster, a host. It has a type |
| Check | One test that a tool runs on a system, with its ID in that tool |
| Source | The tool that runs a check. A security stream in Loki is also a source: its newest line is its result |
| Result series | A Prometheus series that gives the result of one check on one system |
| Control series | A Prometheus series that gives the state of one safeguard on one system |
| Cycle | The time between two runs of a check |

## The registry

### `systems.yaml`

One line for each system.

| Field | Value |
| --- | --- |
| `name` | The name that this repository uses already: the cluster name, the host in the Ansible inventory, the Terraform root. It is the value of the label `system` |
| `type` | One of the fixed types below |
| `site` | The cluster name of the site (`homelab-wsl`, `homelab-wsl-02`), or `none` for a system that is on no site |
| `domain` | The domain of the system. It names no person and no age group |
| `sources` | Optional. The sources that report on the system today. The rule for a late result watches each of them |

The types are fixed: `aws-account`, `cloudflare-account`,
`github-organization`, `terraform-cloud-organization`, `cluster`,
`windows-host`, `wsl-distro`, `laptop`, `router`, and `other` for a layer
with no check.

### `sources.yaml`

One line for each source. The numbers of the cycle table below are in this
file only.

| Field | Value |
| --- | --- |
| `name` | The value of the label `source` |
| `title` | The name that the board shows |
| `kind` | `results`: the source gives a result series for each check. `stream`: each check is a security stream, and its newest line is its result |
| `cycle` | The time between two runs (`24h`, `15m`), or `continuous` |
| `late_after` | The age at which the newest result of the source is late |
| `checks` | For the kind `stream` only: each stream, with its own `cycle` and `late_after`. `system_label` is optional: the Loki label whose value is the name of the system, for a stream that Loki holds one time for each system. A stream with none is one stream for each system that has it as a check |
| `by_type` | Optional. A different `late_after` for one type of system |

### `controls.yaml`

One line for each safeguard: the 48 safeguards of Implementation Group 1
that apply. Control 14 trains a workforce, so its safeguards are not in the
registry.

| Field | Value |
| --- | --- |
| `id` | The ID of the safeguard, as a string (`"8.2"`). It is the value of the label `safeguard` |
| `title` | A short label in the words of this repository. It is not the text of CIS |
| `ig` | The lowest Implementation Group that holds the safeguard |
| `function` | The function of NIST CSF 2.0: `govern`, `identify`, `protect`, `detect`, `respond` or `recover` |
| `nist_800_53`, `attack` | Optional. The controls of NIST SP 800-53 that the checks test, and for detection the ATT&CK techniques |
| `checks` | One list for each type of system, except `other`. Each entry is `{ source, check }` |

Each type is present in `checks`, so an empty list is a statement: no check
tests the safeguard on that type, and each system of the type shows "no
evidence" for it.

```yaml
- id: "8.2"
  title: Audit logs collected
  ig: 1
  function: detect
  checks:
    aws-account:
      - { source: prowler, check: cloudtrail_multi_region_enabled }
    cluster:
      - { source: stream, check: k8s-audit }
    laptop: []
    # ... each other type
```

### What a check ID is

| Source | The `check` of a line |
| --- | --- |
| `prowler` | The check ID of Prowler, such as `cloudtrail_multi_region_enabled` |
| `trivy` | A control of the Trivy Operator compliance report `k8s-cis-1.23`, as `k8s-cis-1.23/<control ID>`. `image-vulnerabilities` is the result of the image scans |
| `host-check` | The name of one test of the host check, such as `firewall-enabled` |
| `api-check` | The name of one test of the API checks, such as `routeros-firmware` |
| `stream` | The value of the Loki label `security_stream`: `k8s-audit`, `routeros`, `cloudtrail` |
| `wazuh` | The ID of a check of the Wazuh SCA policy of the host, such as `26001`. `inventory-hardware`, `inventory-packages` and `vulnerabilities` are the state that the agent reports apart from SCA |

## The series

This is the contract between the sources, the generated rules and the board.
The names and the labels stay. A new source maps to them.

| Series | Who writes it | Labels | Value |
| --- | --- | --- | --- |
| `homelab_security_check_result` | Each source of the kind `results` | `source`, `check`, `system` | The number of resources that fail the check. 0 is a pass, and a value above 0 is a fail |
| `homelab_security_last_result_timestamp_seconds` | Each source | `source`, `system`. For a source of the kind `stream`, also `check` | The Unix time of the newest result of the source for the system |
| `homelab:security_system_info` | The generated rules | `system`, `system_type`, `site`, `domain`, `k8s_cluster` | 1 for each system of the registry |
| `homelab:security_control_state` | The generated rules | `safeguard`, `system`, `system_type`, `k8s_cluster` | The state of one safeguard on one system |
| `homelab:security_safeguard_state` | The generated rules | `safeguard`, `line`, `title`, `ig`, `function`, `k8s_cluster` | The state of one safeguard over the systems that have a check for it |
| `homelab:security_safeguard_has_result` | The generated rules | `safeguard`, `ig`, `k8s_cluster` | 1 when one system or more has a fresh result for the safeguard, and 0 when none has |
| `homelab_security_accepted_until_timestamp_seconds` | The converter, from the list of accepted findings | `source`, `check`, `system` | The Unix time at which one accepted finding ends |

The first two are the result series and the series with the time of the last
result. The fourth is the control series.

### The values of state

| Value | State | Meaning |
| --- | --- | --- |
| 0 | No evidence | No fresh result exists. This is also the state of a type with an empty list |
| 1 | Pass | A fresh result exists, and each check of the line has 0 |
| 3 | Fail | A fresh result exists, and one check or more is above 0 |

The values are those of the state scale of the dashboards
(`observability/dashboards/design.py`), where 2 is "Degraded". A control has
no such state, so 2 is not in use.

A system with no result gets "no evidence", and never a pass. A result
counts only while the time of the last result of its source is fresh: not
older than `late_after`, and not more than 5 minutes in the future. A result
with no time is "no evidence". When two sources test one line, one fresh
source is enough, and a fail wins.

### The state of a safeguard over its systems

`homelab:security_safeguard_state` is the state of one line of the registry.
A system counts for a line when the line has a check for the type of the
system. A system of a type with an empty list has no check to run, and does
not count.

| State of the line | When |
| --- | --- |
| Fail | One system or more that counts has a fail |
| No evidence | No system fails, and one system or more that counts has no fresh result. Also a line with no check for any system |
| Pass | Each system that counts has a pass |

So a line is a pass only when each check that can run did run. One system
with a pass does not make the line a pass while a second system with a check
has no result.

`homelab:security_safeguard_has_result` says a different thing: that one
system or more has a fresh result for the line, a pass or a fail. The count
of lines with a result is the sum of this series. A line can have a result
and the state "No evidence" at the same time.

### What a source must do

- **Use the names of the registry.** `source` is the `name` of the source in
  `sources.yaml`, `check` is the check ID of a line in `controls.yaml`, and
  `system` is the `name` of the system in `systems.yaml`. A result for a
  system that the registry does not name gives no line.
- **Give one result series for each check and system.** Serve the newest
  result until the next run. A source can add labels of its own. The rules
  read only the three labels of the contract. A source with a severity for
  each check gives it in the label `severity`, in lower case: `critical`,
  `high`, `medium`, `low`, `informational` or `unknown`, one value for each
  check and system. The alert for a new finding of high severity reads it.
- **Give the time of the last result for each system.** It is the time at
  which the tool made the result, not the time of the scrape. A source that
  does not know the time serves no time. The rules then read the source as
  late.
- **A stream has a time, and no result series.** Its `check` is the value of
  the label `security_stream`, and its time is the time of its newest line.
  The converter serves it ([`converter/README.md`](converter/README.md)): a
  part that writes a stream sets the labels that the stream map names.
- **Bring the series to site 2.** The generated rules run in the Prometheus
  of site 2 (`homelab-wsl-02`). A source that a Prometheus of site 1 scrapes
  adds its two series to the `peer-federate` scrape job of
  `kubernetes/apps/monitoring/kube-prometheus-stack`.

The generated series carry `k8s_cluster`, as each series of this repository
does. Its value is the site whose Prometheus runs the rules. The label
`site` of `homelab:security_system_info` is the site of the system.

## The cycle of each source

`sources.yaml` holds these numbers. Change them there.

| Source | `name` | Cycle | Late after |
| --- | --- | --- | --- |
| Prowler | `prowler` | 24 hours | 36 hours |
| Trivy Operator | `trivy` | 24 hours | 36 hours |
| The host check | `host-check` | 6 hours | 12 hours |
| The API checks | `api-check` | 24 hours | 36 hours |
| The audit log of each cluster | `stream`, check `k8s-audit` | Continuous | 15 minutes |
| The router log | `stream`, check `routeros` | Continuous | 15 minutes |
| The AWS trail | `stream`, check `cloudtrail` | 15 minutes | 2 hours |
| Wazuh | `wazuh` | 12 hours | 30 minutes, and 7 days for a laptop |

## The host check

The source `host-check` runs on each host and each WSL distro of both
sites, with the same settings. It writes a `.prom` file for the textfile collector of
`windows_exporter` (`infrastructure/ansible/roles/windows_monitoring`).
homelab-wsl scrapes the exporter of both Windows hosts, and `peer-federate`
of homelab-wsl-02 brings the two series of the registry to its rules.

| Where | What runs | File |
| --- | --- | --- |
| Each Windows host | The SYSTEM scheduled task `tnwks-host-check`, every 6 hours and 10 minutes after boot (role `windows_host_check`) | `textfile_inputs\tnwks_security_host_check.prom` |
| Each WSL distro | The systemd timer `tnwks-host-check`, as `wsl_distro_user`, every 6 hours and 10 minutes after boot (role `wsl_host_check`) | `textfile_inputs\wsl-distro\tnwks_security_host_check_distro.prom`, through `/mnt/c` |

The collector reads the subdirectories of `textfile_inputs` too. The
distro's file has its own name, because the collector refuses two files
with one name. The series that both files have use the same text, because
the collector refuses a series with two texts.

Each check gives the number of items that fail it, and 0 is a pass. Each
result carries the label `severity`. `SecurityFindingNew` reads `high` and
`critical` only, so the checks that section 4.1.4 of Design Doc 0004 names
for the alert are `high`, and old signatures, which it does not name, are
`medium`: they show on the board and do not alert.

| Check | What it reads | What fails | Severity |
| --- | --- | --- | --- |
| `defender-enabled` | `Get-MpComputerStatus` | Each of the Defender service, the antivirus and real-time protection that is off | `high` |
| `defender-signatures` | `Get-MpComputerStatus` | Signatures older than 7 days, the age at which Defender counts them as out of date | `medium` |
| `firewall-enabled` | `Get-NetFirewallProfile -PolicyStore ActiveStore` | Each of the Domain, Private and Public profiles that is off | `high` |
| `updates-pending` | An online search of Windows Update for software updates that are not installed, not hidden and not optional | Each such update released more than 14 days ago (Design Doc 0004, section 4.1.4) | `high` |
| `updates-pending` (distro) | `apt-get -s dist-upgrade`, a simulation that installs nothing, on the package lists of the distro | Each package whose upgrade this check first saw more than 14 days ago. A package list does not say when an upgrade came out, so the check keeps the first time it saw each upgrade in `/var/lib/tnwks-host-check/state.json`, and the clock of a package starts at the first run that sees it | `high` |

The file also has series of the host check itself, which no rule of the
registry reads: each protection and profile, the time of the signatures and
of the newest scan, the count of pending updates, the time of the newest
install, a restart that an update waits for, and
`homelab_security_host_check_failed_queries`.

**A query that fails gives no result.** When one query of a check fails,
the run writes no result series and no time, and
`homelab_security_host_check_failed_queries` says how many failed. The rules
then read each line of the host as "No evidence", and
`SecurityResultLate` fires for a host that lists `host-check` in its
`sources`. The run never writes 0 for a check
that it could not do. A fact that no check reads (the newest install, a
pending restart) only leaves out its own series when its query fails. In a
distro, package lists older than 72 hours, or a state file that can not be
read, are a failed query too: a simulation on old lists can not see a new
upgrade.

SYSTEM runs the script from `C:\ProgramData\tnwks-host-check`. The role sets
the rules of that directory on each run: no inherited rule, the
Administrators own it, SYSTEM and the Administrators have full control, and
Users may read. A file below it with a rule of its own, or another owner,
gets the rules of the directory again. The account of the WSL distro may
write only its own directory of the textfile collector.

**The first result, then the source.** A host lists `host-check` in
`sources` of `systems.yaml` only after its first result is on the board of
homelab-wsl-02, in a pull request of its own. Before that, the rule for a
late result would fire at once. The results reach the lines of the registry
without it: `sources` only says which sources the rule for a late result
watches.

To run it now on a host: `Start-ScheduledTask -TaskName tnwks-host-check`.
In a distro: `sudo systemctl start tnwks-host-check.service`.
The way back (Design Doc 0004, section 4.3): set `wsl_host_check_state:
absent` and `host_check_state: absent` in the defaults of the two roles,
which removes the timer, the task, the scripts and the files on each host
and distro, and remove `host-check` from `sources` of the hosts and distros
in `systems.yaml`, in the same pull request.

## The generator

`generate.py` reads the registry and writes seven outputs. It uses the Python
standard library and PyYAML, from the pinned project of `observability/`.

```bash
uv run --project observability python security/generate.py          # write the outputs
uv run --project observability python security/generate.py --check  # fail when one is stale
uv run --project observability pytest security/tests -q
```

| Output | File | What it is |
| --- | --- | --- |
| Rules | `kubernetes/apps/monitoring/security-registry/rules/rules.yaml` | One recording rule for each safeguard and type, the state of each safeguard over its systems, whether it has a result, and the rule for a late result |
| Scan lists | `generated/scan-lists.json` | The checks that Prowler runs for each provider, in the form of its `--checks-file` |
| The scan list of each provider | `kubernetes/apps/security/prowler/app/config/checks-<provider>.json` | The same checks, one file for each provider, for the ConfigMap of the Prowler runs (`kubernetes/apps/security/prowler/README.md`) |
| The Wazuh map | `generated/wazuh-map.json` | The safeguards of each host check: the CIS tags of the release in `catalog/wazuh.json`, carried to the same check IDs, with the lines of the registry. And the checks with no tag |
| Board input | `generated/board.json` | The safeguards, the systems and the cycles that the board shows |
| The list of gaps | `generated/gaps.json` | Each check of a tool that no line names. The quarterly review reads it |
| The maps of the converter | `kubernetes/apps/security/converter/app/config/streams.json` and `registry.json` | Each security stream of each system that has it as a check, with its `late_after` and the Loki selector of its lines. And the names of the sources, the systems and the series, that the converter takes a value of a file against. The converter reads both |

The generator also writes the copy of `converter/converter.py` that the pod
of the converter runs, next to the stream map. Kustomize takes a file only
from the directory of its app, so the tested file and the file that runs are
two files, and `--check` holds them equal.

Never edit an output. Change the registry, run the generator, and commit the
outputs with the change. CI runs `generate.py --check` in the `contracts`
job of `.github/workflows/observability.yaml`, so a change to the registry
without its outputs fails. The same job runs the tests of the generator and
the tests of the rules.

### What the generator checks

- **The schema.** Each file must fit its entry in `registry/schema.json`. A
  line that does not fit fails CI. The schema has no field for a result.
- **The three files agree.** A line names a source of `sources.yaml`. A
  stream is a stream of `sources.yaml`. A system lists only a source that
  has a check for its type. The safeguards are in the order of their IDs.
- **A check exists.** For a source with a file in `catalog/`, each named
  check must be in the check list of its tool for that type.

### The rules

For each safeguard and each type that has a system, one recording rule
reads the result series of the checks of the line and writes
`homelab:security_control_state`. The last term of each rule is the list of
systems of the registry, with the value 0. So each system has a line for
each safeguard, and a system with no fresh result has "no evidence".

Two more rules for each safeguard read the control series: one writes the
state of the line over the systems that count, and one writes whether the
line has a result.

One alert rule, `SecurityResultLate`, watches the time of the last result of
each source in `sources` of each system. For each pair it has two terms:

- `time() - last_over_time(<time>[10m]) > <late_after>`: the newest time is
  older than the limit of its source.
- `absent_over_time(<time>[10m])`: the source served no time. This is
  `absent()` with a stated window, so a source that serves no time reads as
  late.

A second alert rule, `SecurityAcceptedFindingExpired`, fires for a system
when the end date of one of its accepted findings has passed
([Accepted findings](#accepted-findings)).

The query window of each rule is 10 minutes. A rule reads the newest sample
in the window, so one missed scrape or a restart of a source is no gap. An
alert waits 10 minutes more before it fires.

### The text of an alert

An alert of the registry says a system, a severity and a link to the board,
and no raw event. The late alert also names the source that is late. No
alert names a check, a finding or a value of a result. The chat message is
the labels and the annotations of the alert, so the rule is on both:

- The labels are `alertname`, `severity`, `system`, and `source` for the
  late alert. The alert for an accepted finding counts by system, so the
  check of the finding is not a label.
- The annotations are `summary`, `description` and `dashboard_url`, and
  their only variable is the system, or the system and the source.

`tests/test_generate.py` holds this rule for each generated alert, and the
tests of the rules compare the whole text of a sample of each alert.

The tests of the rules are generated too (`generated/rule-tests.yaml`,
`generated/rule-tests-every-source.yaml`), and `promtool test rules` runs
them in CI. They prove that an absent result gives "no evidence" and never a
pass, that a line is a pass only when each system with a check passes, and
that a source with no time fires the alert, and that a stream with a line
for one cluster only is late for the other. The second file tests
the rules of the registry with each source in `sources`, so the alert has a
test before the first source runs.

### The catalogues

`catalog/<source>.json` is the check list of a tool for each type of system,
at one release. The generator needs it for the list of gaps, and to refuse a
check that the tool does not have.

| File | Release | Made from |
| --- | --- | --- |
| `catalog/prowler.json` | Prowler 5.49.0 | The check directories of the AWS, Cloudflare and GitHub providers |
| `catalog/trivy.json` | Trivy Operator 0.36.1 | The controls of the compliance report `k8s-cis-1.23` that have an automated check |
| `catalog/wazuh.json` | Wazuh v4.14.10 | The check IDs of the SCA policies for Windows 11 and Ubuntu 24.04, and the `cis_csc_v8` tag of each |

When a tool moves to a new release, update its catalogue in the same pull
request. A check that the new release adds then shows in the list of gaps
until a line names it.

## The board

The board `Security` (`tnwks-security`) is in Grafana with the other boards
of the repository: `observability/generate.py` writes it from
`observability/dashboards/boards/security.py` to
`kubernetes/apps/monitoring/grafana/dashboards/`. It reads the names of the
series and the values of state from `generated/board.json`, and its lines
are series of the generated rules. A new safeguard, system or check needs no
change to the board.

| Tab | What it shows |
| --- | --- |
| Safeguards | One line for each safeguard with its state, and three counts: the lines with a result, the lines that pass and the lines that fail |
| Systems | Each failing safeguard with its system, the count of safeguards with a result for each system, and the systems of the registry |
| Sources | The age of the last result of each source for each system, the sources that are late, and the count of failing checks |
| Accepted findings | The count of accepted findings, in all and by system, and the count past their end date |

Before a source reports, the first tab shows each safeguard of the registry
with the state "No evidence". The count of lines with a result is the sum of
`homelab:security_safeguard_has_result`. It is apart from the count of lines
that pass, because a line with a result on one system is a pass only when
each system with a check for it passes. The board shows a state and a count. The
security stream `findings` in Loki has the text of each finding.

## When an alert fires

| Alert | What it means | What to do |
| --- | --- | --- |
| `SecurityResultLate` | The newest result of a source for a system is older than `late_after` of the source, or the source serves no time. The safeguards that rest on it show "No evidence" | Open the tab Sources of the board. Then look at the last run of the source: its Job or its pod in the namespace `security`, or the agent on the host. The alert ends when the source reports again |
| `SecurityAcceptedFindingExpired` | An accepted finding of the system has passed its end date | The owner reads the line in the list. The owner sets a new end date with a reason, or removes the line and the cause of the finding |
| `SecurityAcceptedListUnread` | The security converter gets no entry from the list of accepted findings, for 30 minutes. The board shows no accepted finding, and the rule of Trivy Operator mutes none | The log of the converter names the line and the kind of its form. Fix that line of the list (`converter/README.md`, "The form that the converter reads") |
| `SecurityAcceptedEntriesSkipped` | Entries of the list do not fit, for one hour, so their findings show as fails | The log of the converter names each entry by its number, and why. Fix those entries (`converter/README.md`, "An entry that the converter skips") |
| `SecurityFindingNew` | A check of high or critical severity on the system fails, and one of these is true: it has more failed resources than at each time of the day before; it had none one hour before; or it had no result in the day before (a new check, or one that comes back after more than a day). Only high and critical alert, as the design asks for "a new failed check of high severity"; the other severities are on the board. A check that fails again after less than one hour at 0 does not alert again. It compares one value for each check (the labels `k8s_cluster`, `source`, `check` and `system`, from the record `homelab:security_check_result_high:max`), so a new pod of a source, with new `pod` and `instance` labels, or a change of the severity of a check, is not a new result. It is in `observability/rules.py`, and each site fires it for its own results. It ends after one hour. On the first day of a source, and on the first day of the record, each system with a check of high or critical severity that fails alerts one time | Open the tab Systems of the board, then the reports of the source (for Trivy Operator, `kubectl get clustercompliancereport k8s-cis-1.23 -o yaml` and the vulnerability reports in the namespace of the workload). Fix the cause, or accept the finding in the encrypted list |
| `SecurityImagesNotScanned` | Images that run on a cluster with Trivy Operator have had no vulnerability report for 36 hours (the `late_after` of `trivy`). While one image has no report, the check `image-vulnerabilities` of the cluster keeps a fail, and shows "no evidence" in place of a pass. It is in `observability/rules.py`; each site fires it for its own cluster | List the scan jobs and the log of the operator (`kubernetes/apps/security/trivy-operator/README.md`, "When a scan fails"). Add the registry to the policy of the scan jobs, or move the image to a public registry. For an image that no scan can read, add an entry to the accepted list (`accepted.json`), an object in the form `{"source": "trivy", "check": "images-not-scanned", "system": "<the cluster>", "reason": "<why>", "until": "YYYY-MM-DD"}`. It mutes the alert of that cluster until its end, and `SecurityAcceptedFindingExpired` tells the owner at the end. The entry also mutes a new image with no report on the cluster, so keep the end near. Its 36 hours start again at the end, and each time the count of images with no report is 0 for one evaluation |

## Accepted findings

A finding that the owner accepts is a line of an encrypted list. The list is
not in the registry, because a line can name an open weakness. An agent can
draft a line. Only the owner accepts one, in a pull request that the owner
merges.

| What | Value |
| --- | --- |
| File | `kubernetes/apps/security/accepted-findings/app/accepted.sops.yaml` |
| Flux Kustomization | `security-accepted-findings` (`kubernetes/apps/security/accepted-findings/ks.yaml`). It decrypts the file with the age key of the cluster |
| Secret | `security-accepted-findings`, in the namespace `security` of homelab-wsl-02 |
| Key of the Secret | `accepted.json`: one JSON array, with one object for each accepted finding |

Each object has these five keys and no other, and each value is a JSON
string. Each reader of the list (the security converter, the Prowler job,
the API checks job) reads it with `json.loads`, never with a YAML parser:


| Field | Value |
| --- | --- |
| `source` | The `name` of the source in `sources.yaml` |
| `check` | The check ID, as in `controls.yaml`. A check that no line names is also possible. `images-not-scanned` mutes `SecurityImagesNotScanned` of the cluster (`trivy` only) |
| `system` | The `name` of the system in `systems.yaml` |
| `reason` | Why the owner accepts the finding. Not empty, and not only space |
| `until` | The end date, exactly `YYYY-MM-DD` with ASCII digits, then read with `date.fromisoformat`. A reader skips an entry whose end has another form (a time, `20270131`, a week date). The finding is past its end date from the start of the next day, UTC |

A reader skips and counts an item that is not an object, a key that is
missing or is not one of the five, a value that is not a string, and an
entry whose value does not fit. A list that is not one JSON array (a comma
at the end, a comment, a byte order mark, a key two times in one object)
gives no entry at all: the converter then serves none, and a Prowler run
fails and mutes nothing. `security/converter/README.md`, "The list of
accepted findings", has each rule and the alerts.

This is the form of the file before encryption. The values are invented:

```yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: security-accepted-findings
type: Opaque
stringData:
  accepted.json: |
    [
      {
        "source": "prowler",
        "check": "example_check_id",
        "system": "example-system",
        "reason": "An invented reason, for the form only.",
        "until": "2027-01-31"
      }
    ]
```

The rule for `kubernetes/` in `.sops.yaml` encrypts the whole of
`stringData`. So the list is one cipher text in git, and no field of a line
is readable there.

No cluster lists the Flux Kustomization before the list has its first file.
To start the list, the owner does these steps in one pull request:

1. Write the file in the form above, with the first accepted findings. The
   converter reads the block form of the example only
   ([`converter/README.md`](converter/README.md), "The form that the
   converter reads").
2. Encrypt it: `sops --encrypt --in-place` on the file.
3. Name the file under `resources` in `app/kustomization.yaml`.
4. Add `ks.yaml` to `kubernetes/clusters/wsl-02/apps/kustomization.yaml`,
   after the line of the namespace `security`.

Each scanner reads the Secret as its list of exceptions. The converter reads
the Secret for Prometheus ([`converter/README.md`](converter/README.md), "The
list of accepted findings"), and serves one series for each entry:
`homelab_security_accepted_until_timestamp_seconds`, with the labels
`source`, `check` and `system`, and the Unix time of the end as its value.
The alert and the board read this series, and nothing else of the list
leaves the Secret.

The rule of the result series of `trivy` reads the last value of this series
of the last day (`last_over_time`), so an accepted finding stays muted while
the series is gone for a while: homelab-wsl reads it through the federation,
and the reader can start again. The cost: an entry that the owner takes out
of the list before its end date stays muted on the board for up to a day.
The end date still holds, as it is the value of the series.

## How to change the registry

Each change is a pull request, and the owner reviews each line.

**A new system of a known type** is one line in `systems.yaml`, and its
entry in the inventory that manages it. The rules and the board select by
the type, so nothing else changes.

**A new check** is one entry under its safeguard and its type in
`controls.yaml`:

1. Add `{ source, check }` to the list of the type.
2. For a new source, add its line to `sources.yaml` with its cycle.
3. Make the source serve the two series of the contract.
4. In the pull request that starts the source on a system, add the source
   to `sources` of that system in `systems.yaml`. From that merge, the rule
   for a late result watches it.
5. Run the generator, and commit its outputs.

A text in this repository says what a part does. It never names a finding
or the result of a check. An accepted finding goes to the encrypted list,
and each line of that list is the owner's.
