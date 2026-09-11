# Shared Terraform modules

Reusable child modules consumed by the root modules under
`infrastructure/terraform/`. Each directory is named for **what it provisions**,
not for the provider it happens to use, so a second Cloudflare module (say
`cloudflare-tunnel`) can land here without the names colliding.

| Module | Consumed by | Provisions |
| --- | --- | --- |
| `cloudflare-zone/` | `infrastructure/terraform/cloudflare` | One Cloudflare zone plus its zone settings, custom WAF ruleset, and DNS records. Instantiated once per domain. |

## Conventions

- One file per concern, matching the root modules: `versions.tf`, `variables.tf`,
  `outputs.tf`, then a file per resource group (`zone.tf`, `dns.tf`, `waf.tf`, ...).
- `versions.tf` declares `required_providers` **without** a version constraint.
  Pinning belongs to the calling root module so every caller resolves one
  consistent provider version.
- No `backend.tf` or `provider` blocks. Child modules inherit both from the caller.
