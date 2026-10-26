# IAM Roles Anywhere (SOPS KMS)

Workstation AWS credentials for SOPS without SSO logins or long-lived keys.
A certificate from this repo's own CA gets a one-hour session in the prod
account as `tnwks-rolesanywhere-sops`, whose only permission is to assume the
management account's `iam-role-sops`, the role in the KMS key policy. sops does
that hop itself: every `.sops.yaml` KMS entry names `iam-role-sops`.

```
cert (CN on the allowlist) -> Roles Anywhere (prod, us-west-2)
  -> tnwks-rolesanywhere-sops (prod)  -- sts:AssumeRole only -->
  -> iam-role-sops (management)  -> KMS key kms-sops
```

A leaked certificate reaches SOPS decryption and nothing else, and never
authenticates into the management account directly.

**Cost:** none. The CA is self-managed (no AWS Private CA), and Roles
Anywhere, IAM and STS carry no charge.

## Files

| File | |
| --- | --- |
| `ca.pem` | CA certificate (public), the trust anchor's bundle |
| `ca-key.sops.yaml` | CA private key, age-encrypted. Only `issue-cert.sh` reads it, streamed, never to disk |
| `issue-cert.sh` | Issues a client certificate for a CN; the client key is generated locally and stays there |

Terraform: `modules/aws/rolesanywhere.tf` (trust anchor, role, profile) and the
`tnwks-rolesanywhere-sops` entry in `modules/aws/identity/iam_policies.tf`
(trust on `iam-role-sops`).

## Set up a workstation

```bash
task aws:ra-cert       # key + 1-year cert in ~/.config/tnwks-ops/rolesanywhere
task aws:ra-profile    # writes the tnwks-sops AWS CLI profile
task aws:ra-check      # session ARN, then the iam-role-sops hop
```

The repo's `.envrc` sets `AWS_PROFILE=tnwks-sops`, so sops (and the AWS CLI)
inside the repo use it. Re-run `task aws:ra-cert` before the cert expires.

## Adding or removing a machine

The role only trusts CNs in `rolesanywhere_sops_common_names`
(`environments/prod/aws/main.tf`). Add a CN there and apply, then issue its
certificate with `task aws:ra-cert RA_CN=<name>`. Removing a CN and applying
revokes it immediately, whatever the certificate's expiry.

If the CA key is ever exposed, generate a new CA, replace `ca.pem` and
`ca-key.sops.yaml`, apply, and reissue every certificate.
