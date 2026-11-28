# prowler

Prowler checks each AWS account and the Cloudflare account of the security
registry each day. GitHub has no run ("GitHub", below). It is the source
`prowler` of the registry (`security/README.md`). Each CronJob runs one
system with the scan list of its provider, one process of Prowler for each
service of the list ("Memory"), and leaves one OCSF file on the
volume of results. The converter reads the file and serves the result
series (`security/converter/README.md`, "The files of results").
On homelab-wsl-02 only.

The release is Prowler 5.49.0, the release of `security/catalog/prowler.json`.
The image is pinned by digest in each CronJob. The digest of a tag comes from
the registry: `crane digest docker.io/prowlercloud/prowler:5.49.0`. A new
release changes the catalogue, the image and the scan lists in one pull
request.

## The runs

| CronJob | System | Provider | Time (UTC) | Credentials |
| --- | --- | --- | --- | --- |
| `prowler-aws-prod` | `aws-prod` | `aws` | 03:17 | The Secret `aws-security-audit-sts` (`../aws-credentials/README.md`) |
| `prowler-aws-management` | `aws-management` | `aws` | 05:17 | The same Secret, and the role `tnwks-security-audit` of the management account (`--role`) |
| `prowler-cloudflare` | `cloudflare` | `cloudflare` | 07:17 | The Secret `prowler-cloudflare`, key `CLOUDFLARE_API_TOKEN` |

The times keep one run at a time on the node.

## The parts

| File | What it is |
| --- | --- |
| `ks.yaml` | The Flux Kustomization `security-prowler`. It waits for `security-converter`, which has the claim of the volume |
| `app/cronjob-aws.yaml` | The two runs of AWS |
| `app/cronjob-cloudflare.yaml` | The run of the Cloudflare account |
| `app/lease-cloudflare-token.yaml` | The end date of the Cloudflare token ("When a token ends") |
| `app/secret-cloudflare.sops.yaml` | The owner's token, encrypted |
| `app/scripts/run.py` | One run: the mutelist, Prowler for each part, the file for the converter, the files older than 14 days, the peak of memory. Tests: `security/tests/test_prowler.py` |
| `app/config/checks-<provider>.json` | The scan list of each provider that has a run (`aws`, `cloudflare`), in the form of `--checks-file`. `security/generate.py` writes them from the registry; never edit them |
| `app/networkpolicy.yaml` | What a run of each provider can reach |

The claim `security-results` is in `../converter/app/pvc.yaml`.

## What a run does

1. **The mutelist.** The script reads the list of accepted findings (the
   Secret `security-accepted-findings`, key `accepted.json`), if it exists.
   Each entry of `prowler` for the system of the run, until the end of its
   day UTC, mutes its check on each resource. That is the only list of
   exceptions: the run always passes its own mutelist, so the default
   mutelist of Prowler is off too. The reason of an entry stays in the
   Secret. A muted finding is in the OCSF file with the status `Suppressed`.
   - Prowler reads a key of its mutelist as a regex, so the key of a check
     is `^<the check, escaped>$`: it mutes that check only, and not a longer
     check that holds its name (`cloudtrail_multi_region_enabled` and
     `cloudtrail_multi_region_enabled_logging_management_events`).
   - A mute lasts until the next run. A run on the last day of an entry
     mutes its check in the file, and the board reads that file until the
     next run: up to a day after the end date. `SecurityAcceptedFindingExpired`
     still fires at the end.
   - The script reads the list with `json.loads`, never with a YAML parser.
     An entry that does not fit (not an object, a field missing, a key that
     is not a field, a value that is not a string, a field empty, the reason
     too, a check that is not a check ID, an end that is not `YYYY-MM-DD` or
     that `date.fromisoformat` refuses) is skipped and counted, as the
     converter does: the log has its number and why, never a value of the
     list. So one bad entry does not stop each run until the list is fixed.
   - A list that can not be read at all (not UTF-8, not JSON, a number too
     long or a nesting too deep for the parser, a key twice in one object,
     not an array) stops the run with code 2 and mutes nothing: the run can
     not know what to mute.
2. **Prowler, in parts.** One process of Prowler for each service of the scan
   list, one after the other, each with the checks of its service in a
   `--checks-file`, the output format `json-ocsf`, `--no-banner` and
   `--ignore-exit-code-3` (a failed check is a result, not an error of the
   run). The service of a check is the first word of its ID, as Prowler reads
   it (`ec2` for `ec2_instance_imdsv2_enabled`). Each part runs, also after a
   part that failed, so the log names each part that fails.
3. **The file for the converter.** Each part writes to the scratch space of
   the pod. The script copies the records of each part, in the order of the
   parts, into one JSON array in `prowler/<provider>/tmp/` on the volume, and
   deletes the output of the part. After the last part it moves the file to
   `prowler/<provider>/<system>.<time>.ocsf.json`, with the time at which the
   scan ended. So the converter reads one whole file for each run, as before.
   Each directory has the mode 0755 and each file 0644, for the converter.
   - A part with no finding writes no file, and adds nothing.
   - **A part that fails fails the run:** Prowler ends with an error or a
     signal, or leaves an empty file or a file that is not one JSON array (the
     converter reads an empty file as a bad file). The run then moves no file,
     and the Job fails. A file with the parts that ended would read as a
     result of the whole list, with the checks of the failed part missing.
   - A run in which no part makes a finding moves no file, and fails: a run
     that checked nothing is no result.
4. **The files of the system older than 14 days** are deleted, and the file
   in `tmp/` of a run that failed.
5. **The log.** A line when each part starts, with its service and its number
   of checks, and a line when it ends or fails, with the peak of resident
   memory of its process. The peak of memory of the container is the last
   line.

**The canary.** With `--canary`, a run that moves a file adds one test
record to it, after the records of the parts: the check `tnwks-canary`, a
`FAIL` of severity `High` on the resource `tnwks-canary`, which names nothing
of the account (`security/README.md`, "The canary"). The log says so in one
line. A run that moves no file adds none. The flag is off, except while a
test runs: today it is off.

**Nothing goes to the vendor.** No run has `--push-to-cloud`, and Prowler
looks for a new release only with `-v`. The network policy names no host of
the vendor.

## What a run can reach

Default-deny (`../namespace.yaml`) denies the rest. Nothing calls a run, and
a run calls no API server (`automountServiceAccountToken: false`).

| Provider | Hosts, port 443 | Why |
| --- | --- | --- |
| `aws` | `*.amazonaws.com`, `*.*.amazonaws.com`, `api.ecr.*.amazonaws.com`, `*.s3.*.amazonaws.com`, `*.s3-control.*.amazonaws.com` | The API of each service in each Region, the global ones (IAM, CloudFront, WAF, S3), the buckets, S3 Control with the account in the name, and the public list of IP ranges |
| `cloudflare` | `api.cloudflare.com` | The API of Cloudflare |

One `*` is one label of a name. The list is the names that the AWS SDK of
Prowler 5.49.0 resolves for each client of the checks in
`app/config/checks-aws.json`, in each Region, with no dual-stack and no FIPS
endpoint. A new release of Prowler or a new check can call a new name: read
the names again with the SDK of the image, or look for a refused DNS name of
the pod in Hubble after the run.

What stays open: `*.*.amazonaws.com` and the bucket patterns permit the
bucket of any owner, and a few public names of other customers (for example
an EC2 name under `compute-1.amazonaws.com`). Prowler reads the buckets of
the account by these names, so no list of names can close this. A changed
image with the audit session could send what it reads there; the image is
pinned by digest, and the session can only read.

DNS goes through Cilium's DNS proxy, as in each app of the namespace.

## Memory

| Run | Request | Limit |
| --- | --- | --- |
| Each AWS run | 704 MiB | 896 MiB |
| The Cloudflare run | 512 MiB | 768 MiB |

**Why a run is in parts.** The first AWS runs scanned the whole list in one
process of Prowler. They ended at the limit they had: both runs of the first
night at 768 MiB (`OOMKilled`), and both runs of the second night at 1536
MiB, each after 4 to 5 minutes in which the working set of the container rose
by about 300 to 400 MiB a minute (Prometheus of homelab-wsl-02). Prowler 5.49.0 frees nothing that it reads until its process
ends:

- **Before the scan,** its modules, the metadata of its checks and its
  compliance frameworks: about 345 MiB.
- **The client of each service.** Prowler makes it when the first check of
  the service runs, and keeps it in a module for the rest of the process
  (`prowler/providers/aws/services/<service>/<service>_client.py`): one boto3
  client for each Region, ten threads, and each resource that it read.
  Measured without an account, the clients of the services of the list in 17
  Regions take about 380 MiB before they hold any resource.
- **Each finding.** A check makes one object for each finding, with its own
  copy of the metadata of the check (`prowler/lib/check/models.py`,
  `Check_Report`): about 8 KiB each, measured with the image. Prowler keeps
  all of them in one list (`prowler/lib/check/check.py`, `execute_checks`),
  and at the end makes two more objects for each (the finding of the output
  and the OCSF record) before it writes the file.

So the memory of one process grows with each service that it reads and each
finding that it makes. One process for each service holds the base, one
service and its findings, and then ends and gives its memory back. A check
can read the client of another service too (an EC2 check can read the
clients of VPC or SSM): that client then lives in the part of that check.

**Measured without an account,** with the image of the CronJob, its scan list
and `moto` (the AWS mock in the image) in place of AWS, in a container with
no network:

| Run | Peak |
| --- | --- |
| The whole list in one process | 2907 MiB, rising with each service |
| One process for each service, each of the 59 services but `ec2` | 450 to 693 MiB of resident memory |
| The `ec2` part | 1778 MiB |

The mock is no account: it serves its own test data for each of its Regions,
and for `ec2` it serves far more snapshots than an account of this size has.
So the figures show the form (the base of each process, and a peak that
follows the size of what the service reads), not the peak of a real run. The
resident memory of a process also counts the pages of its libraries, which
the working set of the container counts too.

What a part costs: the start of each process. With the mock, the 60 parts
took 851 seconds and the whole list in one process 84 seconds: about 13
minutes more. Against AWS each start also reads the account and its Regions,
and for the management account assumes the role again. That stays far under
`activeDeadlineSeconds` (3 hours), and the run of the management account
starts two hours after the run of prod.

**The first runs in parts** (2026-11-16, homelab-wsl-02): each AWS run took
about 20 minutes, and each of its 60 parts ended. The largest parts, by the
peak of the process of the part, for `aws-prod` and `aws-management`: `ec2`
491 and 497 MiB, `rds` 459 and 462 MiB, `efs` 420 and 426 MiB, `ssm` 410 and
417 MiB. The four runs of that day:

| Run | Peak of the container (last line of the log) | Working set (Prometheus, one sample a minute) |
| --- | --- | --- |
| `aws-prod`, 07:50 UTC | 682 MiB | 646 MiB |
| `aws-management`, 08:11 UTC | 497 MiB | 446 MiB |
| `aws-prod`, 08:47 UTC | 491 MiB | 486 MiB |
| `aws-management`, 09:47 UTC | 498 MiB | 492 MiB |

The peak of the container counts the page cache too: the 682 MiB of the first
run of the day holds the cache of its joined file.

- **The limit** is the highest peak and 25 %, rounded up to a multiple of 64
  MiB: 682 MiB and 25 % is 853 MiB, so 896 MiB.
- **The request** is the highest working set, rounded up to a multiple of 64
  MiB: 646 MiB, so 704 MiB. With a lower request a run is over its request,
  and among the first pods that the kubelet evicts when the node is short of
  memory.

**Read a run:**

1. The log of the run, in the Loki of homelab-wsl-02. The pod of a Job is
   deleted within a minute of its end, as the threshold of terminated pods of
   the cluster is 1 (`terminated-pod-gc-threshold` in
   `talos/wsl-02/patches/controlplane.yaml`). The Job stays a day, but no
   series of the pod and no log on the node stays: the log in Loki is what
   stays. Its stream has no label of the pod. Each line is the JSON of Vector,
   with the text in `message` and the name of the pod in
   `kubernetes.pod_name`. Each run has a pod of its own, so one run is the
   lines of one pod name. The runs of prod, each with its pod name:
   `{namespace="security", app="prowler"} | json | kubernetes_pod_name=~"prowler-aws-prod-.+" | line_format "{{.kubernetes_pod_name}} {{.message}}"`.
   A run starts with `prowler aws for <system>: ...` and ends with
   `memory peak of the container: N MiB`. Each part has
   `part N of M: <service>, K checks: starts`, then `ends` or `fails` with
   the peak of memory of the part. A part that the kernel stops at the limit
   stops the whole container, with the script: its `starts` line is then the
   last line of the run.
2. The same in Prometheus: one rise and fall of
   `container_memory_working_set_bytes{namespace="security",container="prowler"}`
   for each part, in the order of the services. The highest is the part to
   look at: `max_over_time(container_memory_working_set_bytes{namespace="security",container="prowler"}[1d])`.
3. **Why a run ended is in its log only.** Prometheus keeps no reason: with
   the pod gone, kube-state-metrics serves no
   `kube_pod_container_status_terminated_reason` and no waiting reason for
   it. A kill at the limit is a kill of the memory cgroup, with the script in
   the same group: the kernel log of the node shows it, and the log of the
   run ends at the `starts` line of the part.
4. A run over the limit ends with the status `OOMKilled`, and `KubeJobFailed`
   fires. A run that fails is not tried again (`backoffLimit: 0`): the next
   run is the next day. The peak of a part comes from the resources of the
   account, so a second try at once would end the same way and read each API
   again.

The limit follows the measure: when a run measures a higher peak, set the
limit to that peak and 25 % in one pull request, with the lines in
`../README.md`.

If one part still reaches the limit, the log names its service. The next
steps, each with its cost:

- **That service in more than one part,** by its checks: each part then reads
  the service again, so it saves only the findings of the other checks.
- **That service in one part for each Region** (`--region`): its global
  checks would then run in each part and give their findings more than once.
- **A higher limit** for the size of that one service, with its measured
  peak.

Ways to bring the peak down that are not in use:

- **Fewer Regions** (`--region` or `--excluded-region`): each Region costs
  about 15 MiB of clients, but the checks would not see a resource in a
  Region left out, and some checks of the registry ask for each Region. Not
  equal coverage.
- **`MALLOC_ARENA_MAX=2`:** fewer heaps of the C library for the threads of
  Prowler. Measured with the mock, the peak of the whole list fell by 6 %. Too
  little to stand for parts.
- **Prowler 5.49.0 has no setting** for its number of threads (10 for each
  service, in the code) and no flag for its number of clients. The run
  writes only `json-ocsf`, and a run with `--checks-file` writes no
  compliance output, so there is nothing to turn off there.
  `--scan-unused-services` is off (the default), which is the setting with
  fewer findings.

## To start a provider

Nothing of a provider runs before its credentials exist. The app has no
CronJob until the owner names it in `app/kustomization.yaml`.

**AWS.** Started: `app/kustomization.yaml` names `./cronjob-aws.yaml`, as
`../aws-credentials` runs on homelab-wsl-02 and has written
`aws-security-audit-sts` (the annotation `security.tnwks.us/expires` of the
Secret has a time). The runs are at 03:17 and 05:17 UTC. To run one now:
`kubectl -n security create job --from=cronjob/prowler-aws-prod prowler-aws-prod-first`.

**Cloudflare.** Started on 2026-11-28: `app/kustomization.yaml` names the
Secret, the Lease and the CronJob. The token has no end date, so the Lease
holds its review date ("A token with no end date"). The run is at 07:17 UTC.
To run one now:
`kubectl -n security create job --from=cronjob/prowler-cloudflare prowler-cloudflare-first`.

The token is the owner's, read-only, with the least scope
that its checks need. Prowler reads it from the environment, and the run
never prints it. In one pull request:

1. Make the token (the tables below). Give it an end date of one year at
   most, and write that date as `spec.renewTime` of
   `app/lease-cloudflare-token.yaml`, in
   the form `2027-11-13T00:00:00.000000Z`. `SecurityTokenExpiring` fires 30
   days before it ("When a token ends").
2. Write the Secret in this form, with the token in place of the text in
   angle brackets:

   ```yaml
   ---
   apiVersion: v1
   kind: Secret
   metadata:
     name: prowler-cloudflare
   type: Opaque
   stringData:
     CLOUDFLARE_API_TOKEN: <the token>
   ```

   as `app/secret-cloudflare.sops.yaml`.
3. Encrypt it: `sops --encrypt --in-place` on the file. The rule for
   `kubernetes/` in `.sops.yaml` encrypts the whole of `stringData`.
4. Name the Secret, the Lease and the CronJob under `resources` in
   `app/kustomization.yaml`, the Secret first.
5. Merge. The run is at the next 07:17 UTC.
6. After its first result is on the board, add `prowler` to `sources` of
   `cloudflare` in `security/registry/systems.yaml`, in a pull request of
   its own, so that `SecurityResultLate` watches it (`security/README.md`,
   "A new check").

The Cloudflare token: **My Profile, API Tokens, Create Custom Token.**

| Field | Value | Why |
| --- | --- | --- |
| Permissions | Account, Account Settings, Read | Prowler finds the account of the token (`accounts.list`) |
| | Zone, Zone, Read | The list of zones |
| | Zone, Zone Settings, Read | The settings that the four checks read: SSL mode, minimum TLS version, Always Use HTTPS, Automatic HTTPS Rewrites |
| Account Resources | Include, the account | One account |
| Zone Resources | Include, All zones from the account | Each zone of the account |

Prowler also asks for the DNSSEC, Universal SSL, rulesets and bot settings
of each zone. This token may not read them, so the log of a run has errors
for those calls. The registry runs no check that needs them. The expected
lines, for each zone, are six `ERROR` lines of the class
`PermissionDeniedError` (HTTP 403): four that start with the ID of the zone
(DNSSEC, Universal SSL, the rate limit rules, the bot settings) and two with
no ID (the firewall rules and the WAF rulesets, both from the rulesets of the
zone). Another class, such as `AuthenticationError` (HTTP 401, a token that
is wrong or has ended), or a 403 for the zone settings, is a fault.

## GitHub

GitHub has no Prowler run. The two checks of the registry for GitHub
(`organization_members_mfa_required` on three lines,
`organization_default_repository_permission_strict` on one) read the
settings of an organization. `iT3E` is a personal account, so Prowler has
no such settings to read: it makes no finding for these checks. With no
finding, Prowler writes no file, so a run would fail each day and fire
`KubeJobFailed`.

The API checks of the security platform take the GitHub lines of the
registry: their workstream gives each line its API check ID in
`security/registry/controls.yaml`, in place of `source: prowler`. Until
then, the lines keep `source: prowler` (a comment there says why). The
system `iT3E` lists no source in `security/registry/systems.yaml`, so no
rule waits for a result of it and no late alert fires.
`security/generate.py` writes a scan list only for the providers with a run
(`PROWLER_RUNS`), and a test fails if a GitHub run, its scan list or its
network policy comes back.

## When a token ends

The Cloudflare token does not renew itself. The owner writes its
end date to the renew time of its Lease (`app/lease-*-token.yaml`), and
kube-state-metrics serves it as `kube_lease_renew_time`, in the form of the
certificates of `../aws-credentials`. The alert `SecurityTokenExpiring`
(`observability/rules.py`) fires 30 days before that date. To renew, make a
new token, put it in its Secret with `sops <file>`, write its end date to the
Lease, and merge them in one pull request. A Lease that nobody set has a date
in the past, so the alert fires at once.

- **A token with no end date.** Make each token with an end, one year at
  most: the end is also the date of its rotation. If a token has no end,
  write a review date one year out to the Lease, and at that date make a new
  token with an end.
- **The Lease belongs to Flux.** git is the one source of the date: Flux puts
  back a `kubectl edit` of the Lease at its next reconcile. Change the date
  in `app/lease-*-token.yaml` only.
- **A test checks the pair.** `security/tests/test_prowler.py` fails if
  `app/kustomization.yaml` lists the CronJob of a provider without its Secret
  and its Lease, or the other way, and it checks the form of the date, not
  its value.

## When a run fails

`KubeJobFailed` fires for the Job. The log of the run says why ("Read a run"):

- **The session.** `aws-security-audit-sts` has `pending`, or its time has
  passed: see `../aws-credentials/README.md`.
- **A host that the policy does not name.** A new service of AWS under
  another domain. Add the name to `app/networkpolicy.yaml` and to the table
  above.
- **Memory.** The status `OOMKilled`: see "Memory", "Read a run".
- **A part.** `N of M parts failed (<services>)`: the line of each part that
  fails says why.

The next run is the next day. The time of the last result then ages, and
`SecurityResultLate` fires after 36 hours: `aws-prod` and `aws-management`
list `prowler` in `sources` of `security/registry/systems.yaml`. The
Cloudflare account lists it in the pull request after its first result
("To start a provider").

## The way back

Remove the CronJob of a provider from `app/kustomization.yaml`: Flux prunes
it, and no run starts. Its files stay on the volume, and the converter reads
the newest until it is late. To remove all: take `ks.yaml` out of
`kubernetes/clusters/wsl-02/apps/kustomization.yaml`.

## Tests

Offline, before a merge:

```bash
uv run --project observability python security/generate.py --check
uv run --project observability pytest security/tests/test_prowler.py -q
kustomize build kubernetes/apps/security/prowler/app
flate test all -p kubernetes/clusters/wsl-02
```

After the first run, with reads only:

1. The Job ends with success, and its log has a line `ends` for each part,
   `wrote prowler/aws/aws-prod.<time>.ocsf.json` and the peak of memory.
2. The converter (when it reads the files) serves
   `homelab_security_last_result_timestamp_seconds{source="prowler",system="aws-prod"}`
   with the time of the name of the file.
3. An accepted check of the system is in the file with the status
   `Suppressed`.
