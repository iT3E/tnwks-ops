# AWS + Cloudflare Terraform

## Layout

```
environments/          thin roots — one per Terraform Cloud workspace
├── bootstrap/         one-shot org setup, applied by hand before anything else
│   ├── aws-init/      tnwks-ops-aws-init       (CLI run, static root keys)
│   └── aws-identity/  tnwks-ops-aws-identity   (org, Identity Center, SOPS KMS)
├── aws-prod/          tnwks-ops-aws-prod       (CI-driven, day-to-day infra)
└── cloudflare/        tnwks-cloudflare-prod_old (local via `task terraform:*`)

modules/               child modules, namespaced by provider
├── aws/               prod account infra (+ oidc/ and identity/ nested)
├── tfc/               Terraform Cloud project, workspaces, variable set
└── cloudflare/        one zone, its settings, WAF and DNS
```

Each environment root holds only `backend.tf`, `providers.tf`, `versions.tf`,
`main.tf` (the module call), and where needed `data.tf` / `outputs.tf` /
`variables.tf`. All resources live in `modules/`. See `modules/README.md` for the
module conventions.

> **Terraform Cloud `working_directory` must point at the environment root.**
> It is a path *relative to the configuration root*, and CI uploads
> `infrastructure/terraform` as that root, so the value is
> `environments/aws-prod`, not the full repo path. This lets relative module
> sources such as `../../modules/aws` resolve inside the uploaded tarball.
>
> For CLI-driven workspaces the same value works: because
> `environments/<env>` is two levels deep, running Terraform from that
> directory makes the CLI upload its parent and grandparent, which is again
> `infrastructure/terraform`.

## Manual bootstrap steps

### Terraform Cloud init

1. Create the `tnwks-ops` organization on app.terraform.io with a CLI-driven
   workspace named `tnwks-ops-init`.
2. Create the workspaces below, each with `working_directory` set to its
   environment root:

   | Workspace | `working_directory` | Execution mode | Driven by |
   | --- | --- | --- | --- |
   | `tnwks-ops-aws-init` | `environments/bootstrap/aws-init` | local | CLI, by hand |
   | `tnwks-ops-aws-identity` | `environments/bootstrap/aws-identity` | remote | API/CLI, by hand |
   | `tnwks-ops-aws-prod` | `environments/aws-prod` | remote | `.github/workflows/terraform-{plan,apply}.yaml` |
   | `tnwks-cloudflare-prod_old` | `environments/cloudflare` | local | `task terraform:{plan,apply}` |

   None of these workspaces currently has a VCS connection; runs are created by
   the GitHub Actions workflows (prod) or from the CLI.

3. Set up the GitHub OAuth connection for VCS runs.

### AWS init — org owner account

4. Create the org owner AWS account.
5. Generate a programmatic access key and secret key for the root user.
6. Add the root credentials to
   `environments/bootstrap/aws-init/secrets.sops.yaml`.
7. Using the `tnwks-ops-aws-init` workspace, apply
   `environments/bootstrap/aws-init` as a CLI run with those root credentials.
   This creates the Terraform OIDC provider and role in the org owner account,
   then the TFC project and workspaces that assume it.
8. Apply `environments/bootstrap/aws-identity` to set up, in the org owner
   account:
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
