# Shared Terraform modules

Child modules consumed by the thin root modules under
`infrastructure/terraform/environments/`. Directories are namespaced by
**provider**, and inside each one there is **one file per piece of
infrastructure** (`cognito.tf`, `ses.tf`, `iam_policies.tf`, `iam_roles.tf`, ...).

```
modules/
├── aws/                 # prod account infra: cognito, ses, iam, acm
│   ├── init/            # org bootstrap: TFC OIDC trust, project, workspaces
│   └── identity/        # organizations, identity center, kms for sops
├── cloudflare/          # one zone + its settings, WAF ruleset and DNS records
└── proxmox/             # legacy, no active callers
```

| Module | Called by | Provisions |
| --- | --- | --- |
| `aws/` | `environments/aws-prod` | Cognito user pool, groups, seeded users, app clients, custom hosted-UI domain, SES domain identity + DKIM, SMTP IAM user, ACM cert for the auth domain |
| `aws/init/` | `environments/aws-init` | IAM OIDC provider trusting Terraform Cloud, the assumable role, the TFC project, workspaces and the project variable set |
| `aws/identity/` | `environments/aws-identity` | AWS Organizations prod account, Identity Center users/groups/permission sets, KMS key backing SOPS |
| `cloudflare/` | `environments/cloudflare` | One Cloudflare zone plus zone settings, custom WAF ruleset and DNS records. Instantiated once per domain |

## Conventions

- **One file per piece of infrastructure.** Split by resource type or concern
  (`iam_policies.tf` separate from `iam_roles.tf`), not one file per provider.
- `versions.tf` declares `required_providers` **without** version constraints.
  Pinning belongs to the calling environment so every caller resolves one
  consistent provider version.
- No `backend.tf` and no `provider` blocks. Child modules inherit both from the
  caller. Where a module needs an aliased provider it declares
  `configuration_aliases` and the environment passes it explicitly.
- **No secret loading inside a module.** `data "sops_file"` uses a path relative
  to the root module, so decryption happens in the environment and values are
  passed in as variables. Keeps modules independent of where secrets sit on disk.
