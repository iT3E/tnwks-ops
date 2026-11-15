# aws-credentials

The AWS sessions of the security platform, on homelab-wsl-02. A CronJob turns
each certificate for IAM Roles Anywhere into an AWS session of 4 hours, in a
Secret that one tool reads. It has the form of the backups
(`kubernetes/apps/kopiur-system/aws-backup-credentials`,
`docs/operations/backups.md`): no IAM user and no long-lived key.

This file is the contract for a tool that reads AWS.

## The sessions

| Tool | Secret (namespace `security`) | Role of the session | What the role can do |
| --- | --- | --- | --- |
| Prowler | `aws-security-audit-sts` | `tnwks-rolesanywhere-security-audit`, in the prod account | Read-only: the AWS managed policies `SecurityAudit` and `ViewOnlyAccess`, and Prowler's additions less its one action that writes. It can also assume `arn:aws:iam::654654220436:role/tnwks-security-audit`, the same read-only role in the management account |
| The trail copy job | `aws-security-trail-sts` | `tnwks-rolesanywhere-security-trail`, in the prod account | Read and list on the trail bucket, and nothing else |

Each Secret has four keys, with the names that the AWS SDKs and the AWS CLI
read from the environment:

| Key | Value |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | The session |
| `AWS_REGION` | `us-west-2` |

- **A tool reads its own Secret, and no other.** `envFrom` with a `secretRef`
  is enough for a Job: it gets the session at its start.
- **A session lasts 4 hours, and the refresher writes a new one every 30
  minutes.** So a Job that starts has more than 3 hours. A pod that runs
  longer mounts the Secret as files and reads them again.
- **A session of the second role in a chain lasts 1 hour.** That is the limit
  of AWS for a role assumed from a role, so the scan of the management account
  is a run of its own.
- **Before the first refresh the keys hold `pending`,** and AWS refuses them.
- The annotation `security.tnwks.us/expires` of a Secret is the end of its
  session, and `security.tnwks.us/certificate-not-after` is the end of its
  certificate.

The roles, their policies and the trust anchor are Terraform:
`infrastructure/terraform/modules/aws/security`.

## The parts

| File | What it is |
| --- | --- |
| `app/cronjob.yaml` | The refresher, every 30 minutes. `SESSIONS` has one entry for each certificate |
| `app/scripts/refresh.py` | Certificate to session to Secret, with `aws_signing_helper`. It never prints a credential. Tests: `security/tests/test_aws_credentials.py` |
| `app/secret-sts.yaml` | The two session Secrets. Flux only creates them, and the refresher owns their data |
| `app/lease.yaml` | One Lease for each certificate. Its renew time is the end of the certificate |
| `app/rbac.yaml` | The refresher may patch those two Secrets and two Leases, by name |
| `app/networkpolicy.yaml` | Out: DNS, `rolesanywhere.amazonaws.com` for the helper, `rolesanywhere.us-west-2.amazonaws.com` for the session, and the API server. Nothing calls it |

The memory limit is 64 MiB, the line of the refresher in `../README.md`.

## When a certificate ends

A certificate lasts one year. The alert `SecurityCertificateExpiring`
(`observability/rules.py`) fires 30 days before its end. The refresher writes
the end of each certificate to the renew time of its Lease, and
kube-state-metrics serves that time as `kube_lease_renew_time`. A CronJob has
no endpoint to scrape, so the Lease carries the date.

**The two certificates end on 2027-11-14 (01:18 UTC)**, so the alert fires
from 2027-10-15. The refresher reads that date from each certificate, so
nothing else holds it.

A run that fails raises `KubeJobFailed`. The sessions that it wrote before
stay valid for up to 4 hours.

To renew, make a new certificate for the same CN (step 2 of "How it
started"), put it in its file with `sops <file>`, and merge. The next run
reads it.

To end the access of a certificate at once, remove its CN from its list in
`infrastructure/terraform/environments/prod/aws/main.tf` and apply.

## How it started (done 2026-11-14)

What was done, in order, in one pull request. The apply of
`environments/prod/aws` made the roles and the profiles first.

1. **The profiles:** the two profiles went into
   `kubernetes/clusters/wsl-02/cluster-settings.yaml`, next to the two roles.
   Their values are outputs of the Terraform workspace `tnwks-ops-aws-prod`:

   | Setting | Output |
   | --- | --- |
   | `RA_SECURITY_AUDIT_PROFILE_ARN` | `rolesanywhere_security_audit_profile_arn` |
   | `RA_SECURITY_TRAIL_PROFILE_ARN` | `rolesanywhere_security_trail_profile_arn` |

2. **The certificates:** the owner made the two certificates, of 365 days,
   with the script of the backups. A key is made in the directory and stays
   there:

   ```bash
   T=$(mktemp -d)
   infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-02-security-audit "$T" 365
   infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-02-security-trail "$T" 365
   ```

3. **The Secret files:** one for each certificate, encrypted from the start.
   `sops <file>` opens an editor on a new file. Its sample content is replaced
   with this form, where `tls.crt` is the content of `$T/<CN>.pem` and
   `tls.key` is the content of `$T/<CN>.key`:

   ```bash
   sops kubernetes/apps/security/aws-credentials/app/secret-certificate-audit.sops.yaml
   sops kubernetes/apps/security/aws-credentials/app/secret-certificate-trail.sops.yaml
   rm -rf "$T"
   ```

   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: aws-security-audit-certificate # or aws-security-trail-certificate
     namespace: security
   type: kubernetes.io/tls
   stringData:
     tls.crt: |
       (the certificate)
     tls.key: |
       (the key)
   ```

4. **The app:** the two files are named under `resources` in
   `app/kustomization.yaml`.
5. **The cluster:** `kubernetes/clusters/wsl-02/apps/kustomization.yaml` lists
   `../../../apps/security/aws-credentials/ks.yaml`, after the line of the
   namespace `security`.

After the merge, Flux makes the parts, and the first run comes within 30
minutes. To see it:

```bash
kubectl -n security get cronjob,job -l app.kubernetes.io/name=aws-credentials
kubectl -n security get secret aws-security-audit-sts aws-security-trail-sts \
  -o custom-columns='NAME:.metadata.name,EXPIRES:.metadata.annotations.security\.tnwks\.us/expires'
```
