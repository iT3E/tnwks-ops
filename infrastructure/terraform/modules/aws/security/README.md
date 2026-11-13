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

Each further copy of management events is billed for each event. So the trail
has a guard: on every plan, Cloud Control lists the trails of both accounts
(`awscc_cloudtrail_trails`), and the plan fails, before it makes anything,
while an account lists a trail other than `tnwks-organization-trail`. The
message names that trail. A trail of the same name that Terraform does not
know fails the apply at `CreateTrail`, and so does a bucket of the same name.

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
