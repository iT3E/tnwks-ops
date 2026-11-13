# The AWS parts of the security platform

Called by `environments/prod/aws`. It spans the two accounts of the
organization, so it takes two `aws` providers: the default one is the prod
account, and `aws.management` is the management account.

| What | Account | Name | File |
| --- | --- | --- | --- |
| The organization trail: management events, every Region, log file validation | management | `tnwks-organization-trail` | `cloudtrail.tf` |
| Trusted access for CloudTrail in the organization | management | `cloudtrail.amazonaws.com` | `cloudtrail.tf` |
| The trail bucket: private, encrypted by S3, objects deleted after 90 days | prod | `tnwks-cloudtrail-<prod account ID>` | `s3_trail.tf` |
| The audit role, for Prowler, through IAM Roles Anywhere | prod | `tnwks-rolesanywhere-security-audit`, profile `tnwks-ops-security-audit` | `rolesanywhere.tf` |
| The trail reader role, for the trail copy job, through IAM Roles Anywhere | prod | `tnwks-rolesanywhere-security-trail`, profile `tnwks-ops-security-trail` | `rolesanywhere.tf` |
| The audit role of the management account, assumed from the audit role of the prod account | management | `tnwks-security-audit` | `iam_roles.tf` |

Every role reads and never writes. The two audit roles carry the AWS managed
policies `SecurityAudit` and `ViewOnlyAccess`, and Prowler's additions to
them less its one action that writes (`iam_policies.tf`). The trail reader
role lists the trail bucket and reads its objects, and nothing else.

## Cost

The first copy of management events is free, so the trail costs the requests
and the storage of its bucket. The module makes no data events, no Insights,
no CloudWatch Logs group and no KMS key, and none of the paid security
services.

## One trail only

Each further copy of management events is billed for each event. So the
module has two checks, one for each account (`cloudtrail.tf`): on every plan
and apply, Cloud Control lists the trails of the account
(`awscc_cloudtrail_trails`), and the run warns while the account lists a
trail other than `tnwks-organization-trail`. The message gives the names. A
check warns and does not stop the run, so read the warnings of the plan
before the merge of a change to this root.

| What exists | What the run shows |
| --- | --- |
| A trail of another name, in either account | The warning `Check block assertion failed`, with the names of the trails |
| An empty list | The warning `Empty result`: the data source fails on an empty list, and that is why this is a check and not a precondition |
| A trail named `tnwks-organization-trail` that Terraform does not know | The apply fails at `CreateTrail` |
| A bucket of the trail bucket's name | The apply fails at `CreateBucket` |

The list is the one that Cloud Control gives in the home Region of the
workspace. Before the first apply, read the trails of each account once by
hand as well:

```bash
aws cloudtrail describe-trails --include-shadow-trails
aws cloudtrail list-trails
```

## The certificates

The two Roles Anywhere roles use the trust anchor and the CA of
`environments/prod/aws/rolesanywhere/`, in the form of the backups
(`docs/operations/backups.md`, "Credentials"). Each role trusts the
certificate CNs that `environments/prod/aws/main.tf` lists for it, one each:
`homelab-wsl-02-security-audit` and `homelab-wsl-02-security-trail`. On
homelab-wsl-02, the credential refresher of the namespace `security` turns
each certificate into a 4-hour session. `issue-cert.sh` of that directory
makes a certificate for a CN.

To end a certificate's access at once, remove its CN from the list and
apply. An empty list removes the role and its profile.

## The way back

Remove the `security` module from `environments/prod/aws/main.tf`, with its
outputs and the `awscc` providers, and apply. The trail, the roles and the
trusted access go. The bucket does not go while it holds objects: its
lifecycle rule empties it within 90 days, and a later apply removes it.
