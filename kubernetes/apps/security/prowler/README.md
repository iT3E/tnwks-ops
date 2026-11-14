# prowler

Prowler checks each AWS account and the Cloudflare account of the security
registry each day. GitHub has no run ("GitHub", below). It is the source
`prowler` of the registry (`security/README.md`). Each CronJob runs one
system with the scan list of its provider, and leaves one OCSF file on the
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
| `app/secret-cloudflare.sops.yaml` | The owner's token, encrypted. Not in git until the owner makes it |
| `app/scripts/run.py` | One run: the mutelist, Prowler, the file for the converter, the files older than 14 days, the peak of memory. Tests: `security/tests/test_prowler.py` |
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
2. **Prowler,** with `--checks-file`, the output format `json-ocsf`,
   `--no-banner` and `--ignore-exit-code-3` (a failed check is a result, not
   an error of the run).
3. **The file for the converter.** Prowler writes to the scratch space of the
   pod. The script copies the OCSF file to `prowler/<provider>/tmp/` on the
   volume, then moves it to `prowler/<provider>/<system>.<time>.ocsf.json`,
   with the time at which the scan ended. Each directory has the mode 0755
   and each file 0644, for the converter. A run that fails moves no file, and
   the Job fails. So does a run in which Prowler makes no finding at all,
   for which it writes no file: a run that checked nothing is no result. A
   run that leaves an empty OCSF file is a failed run too: the converter
   reads an empty file as a bad file.
4. **The files of the system older than 14 days** are deleted.
5. **The peak of memory** of the container is the last line of the log.

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

The limit is 768 MiB for each run (`../README.md`, "Memory"), with a request
of 512 MiB. A run of Prowler on one cluster measured 466 MiB at peak. A run on
an AWS account is not measured, so the first run measures it:

1. The last line of the log of the run: `memory peak of the container: N MiB`
   (the `memory.peak` of its cgroup).
2. The same in Prometheus:
   `max_over_time(container_memory_working_set_bytes{namespace="security",container="prowler"}[1d])`.
3. A run over the limit ends with the status `OOMKilled`, and
   `KubeJobFailed` fires.

When the peak is near the limit, raise the limit of both runs in one pull
request, inside the 6 GiB of homelab-wsl-02, and change the line in
`../README.md`.

## To start a provider

Nothing of a provider runs before its credentials exist. The app has no
CronJob until the owner names it in `app/kustomization.yaml`.

**AWS.** After `../aws-credentials` runs on homelab-wsl-02 and its first run
has written `aws-security-audit-sts` (the annotation
`security.tnwks.us/expires` of the Secret has a time):

1. Add `- ./cronjob-aws.yaml` under `resources` in `app/kustomization.yaml`.
2. Merge. The first runs are at the next 03:17 and 05:17 UTC. To run one now:
   `kubectl -n security create job --from=cronjob/prowler-aws-prod prowler-aws-prod-first`.

**Cloudflare.** The token is the owner's, read-only, with the least scope
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
(`organization_members_mfa_required`,
`organization_default_repository_permission_strict`) read the settings of
an organization. `iT3E` is a personal account, so Prowler has no such
settings to read: it makes no finding for these checks. With no finding,
Prowler writes no file, so a run would fail each day and fire
`KubeJobFailed`. The API checks of the security platform take the GitHub
lines of the registry. Until then, the system `iT3E` lists no source in
`security/registry/systems.yaml`, so no rule waits for a result of it and
no late alert fires. `security/generate.py` writes a scan list only for the
providers with a run (`PROWLER_RUNS`), and a test fails if a GitHub run, its
scan list or its network policy comes back.

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

`KubeJobFailed` fires for the Job. The log of its pod says why:

- **The session.** `aws-security-audit-sts` has `pending`, or its time has
  passed: see `../aws-credentials/README.md`.
- **A host that the policy does not name.** A new service of AWS under
  another domain. Add the name to `app/networkpolicy.yaml` and to the table
  above.
- **Memory.** The status `OOMKilled`: see "Memory".

The next run is the next day. The time of the last result then ages, and
`SecurityResultLate` fires after 36 hours.

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

1. The Job ends with success, and its log has `wrote prowler/aws/aws-prod.<time>.ocsf.json`
   and the peak of memory.
2. The converter (when it reads the files) serves
   `homelab_security_last_result_timestamp_seconds{source="prowler",system="aws-prod"}`
   with the time of the name of the file.
3. An accepted check of the system is in the file with the status
   `Suppressed`.
