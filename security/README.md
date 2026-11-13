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
| `checks` | For the kind `stream` only: each stream, with its own `cycle` and `late_after` |
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
| `homelab:security_safeguard_state` | The generated rules | `safeguard`, `line`, `title`, `ig`, `function`, `k8s_cluster` | The state of one safeguard: the worst state of its systems that have a result |

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

### What a source must do

- **Use the names of the registry.** `source` is the `name` of the source in
  `sources.yaml`, `check` is the check ID of a line in `controls.yaml`, and
  `system` is the `name` of the system in `systems.yaml`. A result for a
  system that the registry does not name gives no line.
- **Give one result series for each check and system.** Serve the newest
  result until the next run. A source can add labels of its own, such as a
  severity. The rules read only the three labels of the contract.
- **Give the time of the last result for each system.** It is the time at
  which the tool made the result, not the time of the scrape. A source that
  does not know the time serves no time. The rules then read the source as
  late.
- **A stream has a time, and no result series.** Its `check` is the value of
  the label `security_stream`, and its time is the time of its newest line.
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

A text in this repository says what a part does. It never names a finding
or the result of a check. An accepted finding goes to the encrypted list,
and each line of that list is the owner's.
