# AWS + Cloudflare Terraform

## Layout

```
environments/          thin roots — one per Terraform Cloud workspace
├── aws-init/          tnwks-ops-aws-init       (CLI run, bootstrap)
├── aws-identity/      tnwks-ops-aws-identity   (VCS run, trigger on aws-init)
├── aws-prod/          tnwks-ops-aws-prod       (VCS run, trigger on aws-identity)
└── cloudflare/        tnwks-cloudflare-prod_old (local via `task terraform:*`)

modules/               child modules, namespaced by provider
├── aws/               prod account infra (+ init/ and identity/ nested)
├── cloudflare/        one zone, its settings, WAF and DNS
└── proxmox/           legacy, no active callers
```

Each environment root holds only `backend.tf`, `providers.tf`, `versions.tf`,
`main.tf` (the module call), and where needed `data.tf` / `outputs.tf` /
`variables.tf`. All resources live in `modules/`. See `modules/README.md` for the
module conventions.

> **Terraform Cloud `working_directory` must point at the environment root**, for
> example `infrastructure/terraform/environments/aws-prod`. CI uploads the whole
> `infrastructure/terraform` tree so that relative module sources such as
> `../../modules/aws` resolve inside the tarball.

## Manual bootstrap steps

### Terraform Cloud init

1. Create the `tnwks-ops` organization on app.terraform.io with a CLI-driven
   workspace named `tnwks-ops-init`.
2. Create the workspaces below, each with `working_directory` set to its
   environment root:

   | Workspace | Working directory | Run type | Trigger |
   | --- | --- | --- | --- |
   | `tnwks-ops-aws-init` | `infrastructure/terraform/environments/aws-init` | CLI | — |
   | `tnwks-ops-aws-identity` | `infrastructure/terraform/environments/aws-identity` | VCS | after `tnwks-ops-aws-init` |
   | `tnwks-ops-aws-prod` | `infrastructure/terraform/environments/aws-prod` | VCS | after `tnwks-ops-aws-identity` |

3. Set up the GitHub OAuth connection for VCS runs.

### AWS init — org owner account

4. Create the org owner AWS account.
5. Generate a programmatic access key and secret key for the root user.
6. Add the root credentials to `environments/aws-init/secrets.sops.yaml`.
7. Using the `tnwks-ops-aws-init` workspace, apply `environments/aws-init` as a
   CLI run with those root credentials. This creates the Terraform OIDC provider
   and role in the org owner account.
8. Apply `environments/aws-identity` to set up, in the org owner account:
   - import the root user's access key and set it to `Inactive`
   - IAM Identity Center, permission set, new OU
   - the prod member account and its cross-account IAM role

### AWS prod account

9. Apply `environments/aws-prod` (Cognito, SES, supporting IAM). Runs
   automatically on PR merge via `.github/workflows/terraform-{plan,apply}.yaml`.

## CLI usage

Prerequisite: `aws configure sso --profile sso`

```bash
aws sso login --profile sso
terraform login                      # paste terraform.io token
cd infrastructure/terraform/environments/<env>
terraform init
terraform plan
terraform apply -auto-approve
```
