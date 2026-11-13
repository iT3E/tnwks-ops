# The converter

The converter is one small service on homelab-wsl-02. A source that exports
no metrics of its own ends here: the converter reads the result of the
source, and serves it in the series of the control registry
([`../README.md`](../README.md), "The series"). The rules and the board read
those series, so they do not know the tools.

It has the form of the evidence collector (`observability/collector.py`):
one Python file on the standard library, with its tests in CI, on the pinned
Python image. It reads, and it never writes to a source. The one thing that it
writes is the security stream `findings`, in the Loki of its site.

| Path | What it is |
| --- | --- |
| `converter.py` | The service |
| `../tests/test_converter.py` | The tests of the stream source, with a fake Loki on the loopback address |
| `../tests/test_converter_accepted.py` | The tests of the list of accepted findings, and of the loop of the parts |
| `../tests/test_converter_files.py` | The tests of the files of results, with invented OCSF files and result files |
| `../tests/test_converter_findings.py` | The tests of the stream `findings`, with a fake push API of Loki |
| `kubernetes/apps/security/converter/` | The Flux Kustomization, the HelmRelease and the network policy |
| `kubernetes/apps/security/converter/app/config/` | What the pod runs: the copy of `converter.py`, `streams.json`, and `registry.json` (the names of the sources, the systems and the series). `security/generate.py` writes them, so never edit them there |

| Source | State |
| --- | --- |
| The security streams | Here. [The stream source](#the-stream-source) |
| The list of accepted findings | Here. [The list of accepted findings](#the-list-of-accepted-findings) |
| The OCSF files of Prowler, and the result file of the API checks | Here. [The files of results](#the-files-of-results). The converter reads them from the volume `security-results` |
| The lines of the failed findings | Here. [The stream `findings`](#the-stream-findings) |
| The state of the Wazuh indexer | Step 2 of the platform |

## The stream source

A security stream is evidence that a log arrives: the audit log of a
cluster, the router log, the AWS trail. Each 5 minutes the converter asks the
Loki of homelab-wsl-02 for the time of the newest line of each stream, for
each system. It serves that time as the time of the last result:

```text
homelab_security_last_result_timestamp_seconds{source="stream",check="k8s-audit",system="homelab-wsl-02"} 1794550000.123
```

| Label | Value |
| --- | --- |
| `source` | `stream`, the name of the source in `registry/sources.yaml` |
| `check` | The value of the Loki label `security_stream`: `k8s-audit`, `routeros`, `cloudtrail` |
| `system` | The name of the system in `registry/systems.yaml` |

The value is the time of the line in Loki, as a Unix time in seconds. It is
not the time of the query, and not the time of the scrape. A stream has no
result series (`../README.md`, "What a source must do").

### The map from a stream to a system

The map is in the registry, and in no second place:

- `registry/controls.yaml` says which type of system has a stream as a
  check.
- `registry/systems.yaml` names the systems of each type.
- `registry/sources.yaml` says how Loki holds the stream. A stream with a
  `system_label` is in Loki one time for each system, and the value of that
  label is the name of the system. A stream with no `system_label` is one
  stream, and its newest line is the result of each system that has it as a
  check.

`security/generate.py` writes the map to
`kubernetes/apps/security/converter/app/config/streams.json`, with the Loki
selector of each entry. Today it gives:

| Stream | System | Loki selector |
| --- | --- | --- |
| `k8s-audit` | `homelab-wsl` | `{security_stream="k8s-audit",k8s_cluster="homelab-wsl"}` |
| `k8s-audit` | `homelab-wsl-02` | `{security_stream="k8s-audit",k8s_cluster="homelab-wsl-02"}` |
| `routeros` | `sce-rtr01` | `{security_stream="routeros"}` |
| `cloudtrail` | `aws-management` | `{security_stream="cloudtrail"}` |
| `cloudtrail` | `aws-prod` | `{security_stream="cloudtrail"}` |

So the part that writes the audit log of a cluster to Loki sets two labels:
`security_stream="k8s-audit"`, and `k8s_cluster` with the name of the
cluster in `systems.yaml`. The trail of the organization is one stream, and
it is the evidence for each AWS account.

To change the label, or to give a stream one line of Loki for each system,
change `system_label` in `sources.yaml`, run the generator and commit its
outputs. Reloader restarts the converter with the new map.

### How a stream starts

The map has each stream of each system from the start. It does not wait for
`sources` in `systems.yaml`. So the order is:

1. The part that writes a stream starts. Its lines get to Loki with the
   labels of the table.
2. The converter serves the time in its next cycle. The tab Sources of the
   board shows its age.
3. The pull request that confirms the stream adds `stream` to `sources` of
   the system in `systems.yaml`. From that merge, `SecurityResultLate`
   watches it.

Before step 1 the converter serves no time for the stream, and no alert
fires, because no system lists the source.

### When the converter serves no time

| Case | What the converter serves | What the rule reads |
| --- | --- | --- |
| Loki has no line of the stream in the window of the query | No time | Late, after the 10 minutes of the query window of the rule |
| Loki gives an error, an answer that does not fit, or no answer in 10 seconds | No time | The same |
| The newest line is older than `late_after` | The time of that line | Late, by its age |
| The converter starts | No time before the end of its first cycle, which starts at once | No gap: the rule reads the newest sample of 10 minutes |
| A stream has lines for one cluster only | A time for that cluster, and none for the other | The other cluster is late |
| The loop of the converter stops | No time after 10 minutes, and `/healthz` fails after 15 minutes, so the kubelet restarts the pod | Late |

The converter keeps the result of its last cycle only, in memory. A result
goes away in the cycle in which Loki has no line or gives no answer. So a
time that the converter serves is always a time that Loki gave in the last
10 minutes, and never the time of an older cycle.

Each query names the labels of its system. An answer with a stream of other
labels, or with a time outside the window of the query, counts as no answer.
So the time of one cluster can not become the time of a second cluster, and
a time can not be in the future.

### The query

One request for each entry of the map, each 5 minutes:

```text
GET /loki/api/v1/query_range?query={security_stream="routeros"} | line_format "-"&limit=1&direction=backward&start=...&end=...
```

- The window ends now. It starts three times the `late_after` of the stream
  before now, and one hour at least: 1 hour for the audit log and the router
  log, 6 hours for the trail.
- `limit=1` with `direction=backward` gives the newest line only.
- `line_format "-"` puts a constant in the place of the line. So Loki sends
  the time and the labels, and no text of an event. No line is in a log of
  the converter.

Loki asks for no login inside the cluster (`auth_enabled: false` in
`kubernetes/apps/monitoring/loki`), so the converter holds no credential.

### The series of the converter about itself

They show that the converter works before a stream exists. They are not in
the contract of the registry.

| Series | Labels | Value |
| --- | --- | --- |
| `homelab_security_converter_streams` | None | The number of entries in the map |
| `homelab_security_converter_last_cycle_timestamp_seconds` | None | The Unix time at which the last cycle ended |
| `homelab_security_converter_stream_query_success` | `source`, `check`, `system` | 1 when Loki gave an answer in the last cycle, with a line or with none. 0 for an error or no answer |
| `homelab_security_converter_files_skipped` | `source` (`prowler`, `api-check`) | The number of files that the converter skipped in its last cycle: the newest OCSF file of a system, or the result file, that does not fit ([A file that does not fit](#a-file-that-does-not-fit)) |
| `homelab_security_converter_accepted_list_read` | None | 1 when the converter read the list of accepted findings in its last cycle, or found no list. 0 when it gave no entry of a list that is there ([A file that gives no entry](#a-file-that-gives-no-entry)) |
| `homelab_security_converter_accepted_skipped_entries` | None | The number of entries of the list of accepted findings that the converter skipped in its last cycle ([An entry that the converter skips](#an-entry-that-the-converter-skips)) |

## The list of accepted findings

The owner keeps the accepted findings in an encrypted list
([`../README.md`](../README.md), "Accepted findings"). Flux decrypts it to
the Secret `security-accepted-findings`, and the pod mounts the Secret at
`/accepted`. Each 5 minutes the converter reads `accepted.yaml` and serves one
series for each entry:

```text
homelab_security_accepted_until_timestamp_seconds{source="prowler",check="example_check_id",system="example-system"} 1801353600
```

| Label | Value |
| --- | --- |
| `source` | The `source` of the entry, a name of `registry/sources.yaml` |
| `check` | The `check` of the entry |
| `system` | The `system` of the entry, a name of `registry/systems.yaml` |

The value is the Unix time at which the finding ends: the start of the day
after `until`, UTC. So `SecurityAcceptedFindingExpired` fires from the first
second of the next day, and a scanner mutes the finding to the end of its
`until` day. Two entries of one finding give one series, with the later end.

Nothing else of the list leaves the file. The `reason` is in no series and in
no log, and a log line about an entry names its number in the list only.

The mount is optional. Before the owner makes the first list the Secret does
not exist, and the converter serves no entry. When Flux makes or changes the
Secret, the kubelet changes the file in the pod within a minute or two, and
the converter reads the file again in each cycle. So the new list is served
in 10 minutes at the latest, with no restart. On a change, Reloader can also
restart the pod.

### The form that the converter reads

The Python image of the converter has no YAML library, so the converter
reads the block form of the example in `../README.md`, and the form that
`yaml.safe_dump` writes, and no other form:

- A YAML list, one entry for each item `- `, with one `key: value` on each
  line. A comment, a blank line and `---` before the list are no problem.
- A value is plain, in single quotes or in double quotes. A quoted value can
  go on over more lines, to its closing quote, as `yaml.safe_dump` writes a
  long `reason`. A block (`|` or `>`) and a plain value can go on over the
  lines that are more indented than the key. The converter reads no key in
  the lines of a value.
- No flow form (`{ }`, `[ ]`), no anchor, no alias, no tag, no tab in the
  indentation, no key two times in one entry.

The reader mutes too little rather than too much. After a line that it does
not understand, it can not know where the next entry starts, so one such
line makes the whole file give no entry. An entry that it reads but that
does not fit is skipped, and the other entries are served.

The tests hold the reader to PyYAML 6. Each field that the converter takes
is the text that PyYAML gives for it, in the entry of the same place, and
the converter takes no entry of a file that PyYAML does not read. The tests
check this on the example of `../README.md`, on lists of `yaml.safe_dump`
with long and wrapped reasons, and on some thousand invented lists with
lines and characters put in.

### An entry that the converter skips

The converter skips an entry, and counts it in
`homelab_security_converter_accepted_skipped_entries`, when:

- `source`, `check`, `system` or `until` is missing or empty, or goes on
  over more lines.
- The source or the system is not in the registry.
- The check is not a check ID: a letter or a digit, then letters, digits and
  `_./:-`, 200 characters at most. So no text of another form reaches a
  label.
- PyYAML reads the plain value as a number, a bool or null, and its text is
  not the same: `0x1A`, `1.5`, `yes`, `~`. Put such a value in quotes.
- `until` is not a date that Python's `datetime.date.fromisoformat` reads.
  The Prowler job (`run.py` of the Prowler app) reads it with the same
  function, so the two agree. It reads `2027-12-31`, and since Python 3.11
  also `20271231` and `'2027-W52-5'`. Write `YYYY-MM-DD`, as in
  `../README.md`.

The other entries are served. A skipped entry has no series, so the board
does not show it, and the rule of Trivy Operator does not mute it: the
finding shows as a fail.

### A file that gives no entry

The converter serves no entry, sets
`homelab_security_converter_accepted_list_read` to 0, and logs the number
of the line and the kind of its form (no text of the file), when the list:

- Has a line of a form that the converter does not read, or a key two times
  in one entry.
- Ends inside a quoted value.
- Has a character that YAML does not let a file hold, an escape that YAML
  does not have, or a plain date that is no day (`2027-02-30`). PyYAML
  stops on each of them, and so does `run.py`.
- Can not be read, is not UTF-8, or is larger than 1 MiB.

With no list (the Secret does not exist) the converter serves no entry and
the value is 1.

## The loop of the parts

One loop runs the parts of the converter one after the other, each 5
minutes: the stream source, the list of accepted findings, then the files of
results. A fault of
one part does not stop the others: the part serves nothing in that cycle,
and the log says the kind of the fault. Each part keeps the result of its last
cycle only, and serves nothing that no cycle replaced in 10 minutes.

## The service

| What | Value |
| --- | --- |
| Where | homelab-wsl-02, the namespace `security`, on `SECURITY_NODE` (`kubernetes/apps/security/README.md`) |
| Flux Kustomization | `security-converter` (`kubernetes/apps/security/converter/ks.yaml`) |
| Workload | The HelmRelease `security-converter`: one pod, as user 65534, with a read-only root file system and no capability |
| Code | The ConfigMap `security-converter-code`: `converter.py`, `streams.json` and `registry.json`. The image is the pinned Python image of the evidence collector, by digest. The converter has no image of its own |
| Port | 9877: `/metrics` and `/healthz` |
| Scrape | The ServiceMonitor `security-converter`, each minute, by the Prometheus of homelab-wsl-02. The rules of the registry run there |
| Service account | The default one of the namespace, with no token in the pod. The converter does not ask the API server |
| Credentials | None. The pod mounts the Secret `security-accepted-findings`, read-only and optional: the list holds no credential |
| Memory | A request of 32 MiB and a limit of 128 MiB, the line of the converter in `kubernetes/apps/security/README.md` |

The CiliumNetworkPolicy `security-converter` names each path. In: the
metrics port, from the Prometheus pods of this cluster and from the host (the
probes of the kubelet). Out: the gateway pods of this cluster's Loki on port
8080, for the queries of the stream source and the push of the stream
`findings`, and DNS. `default-deny` of the namespace denies each other path.

## Run the tests

```bash
uv run --project observability pytest security/tests -q
uv run --project observability python security/generate.py --check
```

The `contracts` job of `.github/workflows/observability.yaml` runs both, with
ruff, on each pull request that changes `security/` or `kubernetes/`. The
second command fails when the copy in `kubernetes/apps/security/converter/`
is not the tested file. The tests of the rules
(`../generated/rule-tests-every-source.yaml`) show that a stream with no time
reads as late.

## The files of results

This is the contract for the two jobs that write files for the converter:
the Prowler job and the API checks job. Both run on homelab-wsl-02. The
converter reads the files each 5 minutes, and serves
`homelab_security_check_result` and the time of the last result:

```text
homelab_security_check_result{source="prowler",check="example_check_id",system="example-system",severity="high"} 2
homelab_security_last_result_timestamp_seconds{source="prowler",system="example-system"} 1794550000.000
```

| Label | Value |
| --- | --- |
| `source` | `prowler` or `api-check`, the name of the source in `registry/sources.yaml` |
| `check` | The check ID: a letter or a digit, then letters, digits and `_./:-`, 200 characters at most |
| `system` | The name of the system in `registry/systems.yaml` |
| `severity` | Of the result series only. The severity of the check, in lower case: `critical`, `high`, `medium`, `low`, `informational` or `unknown`. The rule of Trivy Operator gives the same values, so one alert for a new finding reads both |

The value of the result series is the number of resources that fail the
check on the system, and 0 when it passes. The time is the time of the newest
whole file of the system. The rules of the registry read `source`, `check`
and `system` only (`max by (system)`), so the label `severity` does not
change the state of a control, and the board counts one series for each
check and system. The converter gives each check one severity. A change of
the severity of a check (a new release of Prowler, or an API job that adds
`severity`) makes a new series, with no history of the day before.

The ServiceMonitor drops the labels of the pod (`pod`, `instance`,
`container`, `endpoint`), so each series of the converter has the labels of
the contract, and `job`, `namespace` and `service`. A restart of the pod
makes no new series, and an alert that compares a series with the day before
finds it. No rule and no board reads the four labels, and one pod runs
(`Recreate`), so two series can not meet.

The converter reads a file again only when its size, its time or its inode
changes. It reads an OCSF file one record at a time, and keeps a hash of 8
bytes for each resource that fails, not its ID. So its memory does not grow
with the size of a record: a test file of 167 MiB took 26 MiB of the process
at its peak, in 0.3 seconds, and a test file at the limit of 200 000 records,
each of them a fail, took 46 MiB. The limit of the pod is 128 MiB.

The converter opens a file of results only when it is a regular file. It
follows no link, and a FIFO or a directory in the place of a file is a file
that does not fit, so no read waits.

A file of results holds results, so it never goes to git.

### The claim

| What | Value |
| --- | --- |
| Claim | `security-results`, in the namespace `security` of homelab-wsl-02 |
| Manifest | In `kubernetes/apps/security/converter/app/`. It comes with the pull request that starts the source of the files, before the first job that writes |
| Class and size | `${STORAGE_CLASS_DEFAULT}`, `ReadWriteOnce`, 1Gi |
| Node | The class puts the volume on the node of its first pod. Each pod that mounts the claim carries the node selector of `SECURITY_NODE`, so one claim holds them all |
| Mount | The whole volume, at a path of the job's choice. Not a `subPath`: the kubelet makes that directory for root, and the class ignores `fsGroup` |
| The converter | Mounts the claim read-only, as user 65534 |

The paths below start at the root of the volume. A job makes its own
directory. Each directory has the mode 0755 and each file the mode 0644, so
the converter can read them.

| Directory | Who writes | What |
| --- | --- | --- |
| `prowler/aws/` | The Prowler job | The OCSF files of the AWS accounts |
| `prowler/cloudflare/` | The Prowler job | The OCSF files of the Cloudflare account |
| `prowler/github/` | The Prowler job | The OCSF files of the GitHub organization |
| `api-check/` | The API checks job | The result file |

The providers are the keys of `prowler` in `../generated/scan-lists.json`. A
new provider gets a directory of its name.

### The OCSF files

- **One file for each run and each system:**
  `prowler/<provider>/<system>.<time>.ocsf.json`. `<system>` is the name of
  the system in `registry/systems.yaml`, such as `aws-prod`. `<time>` is the
  UTC time at which the scan ended, as `YYYYMMDDTHHMMSSZ`.
- **The content** is the JSON array that Prowler writes with the output
  format `json-ocsf`, with no change.
- **Whole files only.** Prowler writes to `prowler/<provider>/tmp/`, and the
  job moves the finished file to its name. A move inside one volume is one
  step, so the converter never reads half a file. A run that fails moves no
  file.
- **The time of the name is the time of the last result** of the source
  `prowler` for that system. The converter reads the newest file of each
  system. It reads no file in `tmp/`, no link, and no file whose name does
  not fit, whose system is not a system of the type of the provider in the
  registry (`aws-account` for `aws`), or whose time is more than 5 minutes in
  the future. The log names such a file one time.
- **The job deletes its files** after 14 days. The converter never writes
  to the volume.

How the converter counts the records of one file:

| Record | What it counts |
| --- | --- |
| `status_code` `FAIL`, and `status` not `Suppressed` | One resource that fails. The resource is the `uid` of the first item of `resources`, the ARN on AWS, or the `finding_info.uid` when it has none. Two records of one resource are one resource. Two resources with one name are two resources |
| `status_code` `FAIL`, and `status` `Suppressed` | Nothing: the list of accepted findings muted it (`kubernetes/apps/security/prowler`). The check has a result |
| `status_code` `PASS` | Nothing. The check has a result |
| `status_code` `MANUAL`, or another one | Nothing. A check with no `PASS` and no `FAIL` record has no result series |

- The check is `metadata.event_code`, the check ID of Prowler.
- The severity of a check is the highest `severity` of its records, in lower
  case. A value that is not an OCSF name is `unknown`.
- A check of the scan list with no record in the file has no result series.
  Prowler writes no record for a check that found no resource, and also none
  for a check that failed to run, so the converter can not say that it
  passed. A line of the registry whose checks all have no record shows "no
  evidence" for the system.

### The result file of the API checks

One file, `api-check/results.json`. Each run writes
`api-check/results.json.tmp` and moves it to that name.

```json
{
  "version": 1,
  "source": "api-check",
  "time": 1794550000,
  "results": [
    { "check": "routeros-firmware", "system": "example-router", "failed": 0 },
    {
      "check": "example-test",
      "system": "example-system",
      "failed": 2,
      "findings": [
        { "resource": "example-1", "message": "An invented text, for the form only." },
        { "resource": "example-2", "message": "An invented text, for the form only." }
      ]
    }
  ]
}
```

| Field | Value |
| --- | --- |
| `version` | 1 |
| `source` | `api-check`, the name of the source in `registry/sources.yaml` |
| `time` | The Unix time, in seconds, at which the run ended |
| `results` | One entry for each test and each system that the run did test. Two entries of one test and one system do not fit |
| `check` | The check ID of the test, as in `registry/controls.yaml` |
| `system` | The name of the system in `registry/systems.yaml` |
| `failed` | The number of resources that fail the test, a whole number. 0 is a pass. It is the value of the result series |
| `severity` | Optional. The severity of the test: `critical`, `high`, `medium`, `low`, `informational` or `unknown`, in lower case. With no `severity` it is `unknown`. The alert for a new finding fires for `high` and `critical` only |
| `findings` | Optional. One object for each resource that fails, with `resource` (what fails) and `message` (why), as text. The job can add fields, and they stay in the file. The converter sends each object as one line of the stream `findings` ([The stream `findings`](#the-stream-findings)) |

- **A test that did not run has no entry.** The job never writes 0 for a
  test that it could not do. A system with no entry gets no time, so the rule
  reads the source as late for that system.
- **`time` is the time of the last result** of each system that has one
  entry or more.
- **No secret** is in the file: no token, and no answer of an API as it came.
- **The list of accepted findings.** The job reads it, as each scanner does
  (`../README.md`, "Accepted findings"): an accepted test counts no resource
  that the list accepts, and lists no finding for it.
- **A system that is not in the registry** has no result and no time. The
  log says it one time.

### A file that does not fit

A file that the converter can not read, or that does not fit its contract,
gives no result series and no time for its system. The converter does not
read an older file in its place. So the rule for a late result fires for the
system, after its `late_after`, and the other files and the other parts go on.
`homelab_security_converter_files_skipped` counts each such file, and the log
of the converter names the file and the reason, with no value of the file.

| File | It does not fit when |
| --- | --- |
| The newest OCSF file of a system | It is not UTF-8, or not one JSON array of objects: a file that a run left half written, an empty file, a comma at the end, text after the array. A record has no `metadata.event_code` that is a check ID, or no `status_code` as text. A record is larger than 4 MiB, the file is larger than 256 MiB, or it has more than 200 000 records or 5 000 checks |
| `api-check/results.json` | It is not JSON, `version` is not 1, `source` is not `api-check`, `time` is not a time up to 5 minutes in the future, or an entry does not have the form of the table above: a `check` that is not a check ID too. The file is larger than 16 MiB, or it has more than 5 000 results |
| Each of them | It is not a regular file: a link, a FIFO, a directory |
| The directory of a provider | It can not be read: it counts as one file of `prowler` that does not fit, and the other providers and the API checks go on |

When the file of a system goes away, its results go away in the next cycle.

## The stream `findings`

Each finding that fails is one line in the Loki of homelab-wsl-02: the
security stream `findings`. The board counts the results, and this stream
says what failed and where. The converter sends the lines of the newest file
of each system, when that file is new to it.

| Label | Value |
| --- | --- |
| `security_stream` | `findings`. So Loki keeps the stream for 90 days, as each security stream (the rule `{security_stream=~".+"}` of the Loki of homelab-wsl-02) |
| `source` | `prowler` or `api-check` |
| `system` | The name of the system in `registry/systems.yaml` |

Each (source, system) is one stream in Loki, and each line has the time of
its file: the time of the name of the OCSF file, or `time` of the result file.

```logql
{security_stream="findings", system="example-system"} | json
```

### What a line holds

A line is a part of the OCSF record of one finding (Detection Finding), as
compact JSON, with the field names of OCSF. It holds what failed, where and
why, and no setting of a resource:

| Field | Value |
| --- | --- |
| `class_uid`, `time` | The class of the record (2004), and the time of the finding, as Prowler wrote them |
| `severity`, `status`, `status_code` | The severity (`High`), `New`, and `FAIL` |
| `status_detail` | Why the check failed, in the words of the tool. The first 8192 characters |
| `metadata.event_code` | The check ID |
| `finding_info.uid`, `finding_info.title` | The ID of the finding, and the title of the check |
| `resources[]` | For each resource (20 at most): `uid`, `name`, `type`, `region`, and `group.name` (the service) |
| `cloud` | `provider`, `region`, and `account.uid` and `account.name` |

Each other text field has its first 1024 characters. A line is 64 KiB at
most in UTF-8, under the line limit of Loki (256 KB): a line that would be
longer has 5 resources and shorter texts (256 and 2048 characters), and then
1 resource (64 and 512 characters). Each other field of the record stays in
the file. These never go to a line: the data of a resource
(`resources[].data`, the settings that Prowler read), the tags of a resource
or an account (`labels`), the remedy and the risk texts of the check, the
compliance map and the other `unmapped` fields, and the organization. The
data of a resource can hold a value of a setting, and a log line holds no
credential (red line R2).

A line of the API checks has the same names: `time` and `severity` of its
result, `status` `New`, `status_code` `FAIL`, `metadata.event_code` (the
check), `status_detail` (the `message`), and `resources[0].uid` (the
`resource`). The other fields of the job stay in the file.

A record that does not fail has no line: `PASS`, `MANUAL`, and a `FAIL`
with the status `Suppressed`, which the list of accepted findings muted.

### How the lines are sent

- **One time for each file.** The converter sends the lines of a newest
  file, and then sends them no more. A new file of the system is a new set
  of lines. The converter sends the newest file only: when a newer file
  comes while the lines of an older one wait for Loki, the older lines are
  not sent. The file stays on the volume for 14 days.
- **After a start** the converter sends the newest file of each system again,
  with the same times and the same text. A query of Loki gives each line one
  time.
- **When Loki does not take them** (no answer, an error 5xx, 429, or 413),
  the lines wait for the next cycle, and
  `homelab_security_converter_findings_pending` is 1 for the stream. The
  result series do not wait for Loki. A 413 says that the body is larger
  than the gateway takes. A batch is far under that limit, so a 413 means
  that the limit changed: the lines wait for that fix, and the series shows
  it, and no line is lost.
- **When Loki refuses them** (a status 4xx other than 429 and 413, such as
  a line more than 7 days old), the converter counts them and does not send
  them again.
- **Each stream on its own.** A fault in the lines of one system (a file
  that changed, a text that is not UTF-8) makes that stream wait, and the
  converter goes on with the next one.
- **Batches of 1 MiB at most on the wire**, to the push API of the gateway
  (`/loki/api/v1/push`). The converter counts the bytes of each line in
  the body, as JSON in ASCII, and not its characters. The gateway of the
  Loki chart takes 4 MiB (`client_max_body_size`). No line and no answer of
  Loki is in a log of the converter: the log says the number of lines, and
  the status.

A day with a few hundred findings that fail is a few hundred kB in Loki.

| Series | Labels | Value |
| --- | --- | --- |
| `homelab_security_converter_findings_pending` | `source`, `system` | 1 when the lines of the newest file of the system wait for Loki, 0 when Loki has them |
| `homelab_security_converter_findings_lines_sent_total` | `source` | The lines that Loki took, since the start |
| `homelab_security_converter_findings_lines_refused_total` | `source` | The lines that Loki refused, since the start |
