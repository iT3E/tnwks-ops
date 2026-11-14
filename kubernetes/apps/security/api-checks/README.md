# api-checks

The API checks test what no scanner of the security platform reads: the
firmware of the router against the releases of its vendor, and the settings
of the Terraform Cloud organization. They are the source `api-check` of the
registry (`security/README.md`). One CronJob runs each day and writes one
result file on the volume of results. The converter reads the file and
serves the result series (`security/converter/README.md`, "The result file of
the API checks"). On homelab-wsl-02 only.

## The tests

| Check | System | What it reads | What fails | Severity |
| --- | --- | --- | --- | --- |
| `routeros-firmware` (safeguard 12.1) | `sce-rtr01` | The version that the router runs: the label `version` of `mktxp_system_uptime` in the Prometheus of homelab-wsl, where mktxp reads the router with its read-only account. The newest stable release and its date: the release feed of the vendor, `https://cdn.mikrotik.com/routeros/latest-stable.rss` | The router runs an older version than a stable release that came out more than 14 days ago (Design Doc 0004, section 4.1.4), of each release that the job has seen | `high`, so a new fail alerts |
| `tfc-mfa-required` (safeguards 6.3 and 6.5) | `tnwks-ops` | `collaborator-auth-policy` of `GET https://app.terraform.io/api/v2/organizations/tnwks-ops` ([the API of HCP Terraform](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/organizations#show-an-organization)) | The policy is not `two_factor_mandatory` | `medium` |

Each test reads, and none changes a setting. The test of the router needs no
credential of the router: mktxp already reads it, and the REST API of the
router is open to the CI runners only.

**A test that does not run has no entry**, never a 0. That is the case when
Prometheus has no series of the router (mktxp did not read it in the last 5
minutes) or two versions of it, when the feed has no stable release with a
date, and when HCP Terraform does not answer 200. The run then fails, which
raises `KubeJobFailed`, and the system reads as late after 36 hours
(`SecurityResultLate`). With no token, the test of Terraform Cloud does not
run, and the run does not fail for it.

The feed holds the newest release only. So the job keeps each newest
release it sees, with the date of the feed (or the time of the run, when
the feed gives a later one), in `api-check/routeros-releases.json` on the
volume, written in one move. A router two releases behind then fails on the
older release, even while the newest is less than 14 days old. The list
starts at the first run: a release older than the first run that the feed no
longer shows is not in it. A list that is there and can not be read gives no
result for the router, and the run leaves it as it is, so no date is lost.
The converter reads `api-check/results.json` only, so it does not read the
list.

**The first result, then the source.** A system lists `api-check` in
`sources` of `security/registry/systems.yaml` only after its first result is
on the board of homelab-wsl-02, in a pull request of its own. Before that,
the rule for a late result would fire at once. The results reach the lines
of the registry without it: `sources` only says which sources the rule for a
late result watches.

## The parts

| File | What it is |
| --- | --- |
| `ks.yaml` | The Flux Kustomization `security-api-checks`. It waits for `security-converter`, which has the claim of the volume |
| `app/cronjob.yaml` | The run, each day at 06:47 UTC, between the runs of Prowler |
| `app/scripts/api_checks.py` | The tests, the list of accepted findings, the list of releases, and the result file. The Python standard library only. Tests: `security/tests/test_api_checks.py` |
| `app/networkpolicy.yaml` | DNS, the Prometheus of homelab-wsl on port 9090 of the address that `peer-federate` scrapes, and `cdn.mikrotik.com` and `app.terraform.io` on 443 |

The claim `security-results` is in `../converter/app/pvc.yaml`. The job runs
as user 65534 with a read-only root, and mounts the whole volume
(`security/converter/README.md`, "The claim").

## The list of accepted findings

The job reads the list (the Secret `security-accepted-findings`, key
`accepted.json`) as the Prowler job does, with the same rules of form
(`security/README.md`, "Accepted findings"). An entry with the source
`api-check`, a check and a system of a test, whose `until` has not passed,
sets that result to 0 with no finding. A list that can not be read stops the
run, which writes no file.

## The token of Terraform Cloud

The owner makes the token. Nothing here makes one.

1. Make a team in the organization `tnwks-ops` for this job, with no access
   to a project or a workspace, and make the team's API token, with an end
   date. A team token acts with the access of its team
   ([API tokens](https://developer.hashicorp.com/terraform/cloud-docs/users-teams-organizations/api-tokens#team-api-tokens)).
   An organization token is broader than this test needs: it manages the
   teams, the members and the workspaces of the organization.
2. Put it in `app/secret-terraform-cloud.sops.yaml`, a Secret
   `api-check-terraform-cloud` with the key `TFE_TOKEN`, encrypted with SOPS
   as each `*.sops.yaml` of this repository, and name the file under
   `resources` in `app/kustomization.yaml`.
3. Merge. To run now:
   `kubectl -n security create job --from=cronjob/api-checks api-checks-first`.
4. After the first result is on the board, add `api-check` to `sources` of
   `tnwks-ops` in `security/registry/systems.yaml`, so the late alert
   watches it, and run the generator.

Until then, `tnwks-ops` has no result for `tfc-mfa-required`. The lines 6.3
and 6.5 then read "no evidence" in place of a pass, as a line is a pass only
when each system with a check passes. The count of lines with a result does
not change: Prowler gives those lines a result.

## When a run fails

The log names each test that did not run and why, with no token and no
answer of an API. A fault of the network, a status that is not 200, or an
answer that does not fit is a test with no result.

## The way back

Remove `- ../../../apps/security/api-checks/ks.yaml` from
`kubernetes/clusters/wsl-02/apps/kustomization.yaml` (Flux prunes the job),
delete `api-check/results.json` and `api-check/routeros-releases.json` from
the volume, and remove `api-check` from
`sources` of the systems in `security/registry/systems.yaml`. The lines 12.1,
6.3 and 6.5 then show "no evidence" for those systems.
