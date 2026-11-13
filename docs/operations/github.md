# GitHub account (Terraform)

The `iT3E` account is a personal GitHub account with no organizations. Its
repositories and the settings that gate this repo (the `main` ruleset, the
deployment environments, the Actions settings) are Terraform like the rest
of the infrastructure: `infrastructure/terraform/environments/prod/github`
holds the values, `infrastructure/terraform/modules/github` the resources.

This first phase only codifies what exists. Every value was read from the
API and is imported as it was, including settings that differ between
repositories for no reason. Making them consistent is a later change, one
PR at a time, each with its own plan.

## What it manages

| Resource | Where | Notes |
| --- | --- | --- |
| `github_repository` | the public repositories, and the private ones already named elsewhere in this repo (outside the paths `.github/privacy-allow` exempts) | Description, homepage, visibility, topics, features (issues, wiki, projects, discussions), merge methods and commit messages, auto-merge, branch deletion and update, sign-off, template, archived. Secret scanning and push protection on public repositories only |
| `github_repository_ruleset` | `tnwks-ops`: `main` | Deletion and force-push blocked, PRs with squash merges only and no approvals, the required checks `Build Success`, `betterleaks / betterleaks` and `privacy / privacy` from GitHub Actions. Admins and one GitHub App bypass it |
| `github_repository_environment` | `tnwks-ops`: `external-contributions`, `openclaw-hooks`, `prod` | The owner as required reviewer of `external-contributions`; `openclaw-hooks` deploys from `main` only; `prod` from protected branches only |
| `github_repository_environment_deployment_policy` | `tnwks-ops`: `openclaw-hooks` | The `main` branch pattern |
| `github_actions_variable` | `tnwks-ops`: `RELEASE_APP_CLIENT_ID`, `UNIFI_TERRAFORM_ENABLED` | Values are public. Other variables stay by hand (below) |
| `github_actions_repository_permissions` | `tnwks-ops` | Actions on, all actions allowed |
| `github_workflow_repository_permissions` | `tnwks-ops` | `GITHUB_TOKEN` read-only by default, can't approve PRs |
| `github_repository_vulnerability_alerts` | `tnwks-ops` | Dependabot alerts on |

The repositories come from one map in `main.tf`. Rulesets, environments,
Actions settings, variables and Dependabot alerts are optional parts of
each entry, so applying them to another repository is a data change. An
entry lists only what differs from a new repository's defaults
(`modules/github/variables.tf`).

On this account's plan the API refuses rulesets, branch protection and
security and analysis settings for private repositories ("Upgrade to GitHub
Pro"), so those entries have none. The module rejects them.

## What stays by hand, and why

The provider has no resource for these:

- private vulnerability reporting;
- the approval policy for workflows from fork PRs;
- artifact and log retention;
- GHCR packages and their visibility;
- SSH signing keys;
- immutable releases;
- code scanning default setup.

Left alone on purpose:

- **The Flux deploy key** on `tnwks-ops`. `flux bootstrap` creates and
  rotates it.
- **Labels.** The `meta-sync-labels` workflow owns them.
- **Releases.** `release.yaml` cuts them.
- **Runner scale sets.** ARC registers them.
- **`GITHUB_TERRAFORM_ENABLED`.** It switches this root on in CI, so it
  can't be managed by the root it switches.
- **Environment variables and all secrets** (Actions, environment,
  Dependabot) and webhooks, for now (later phases).

Account-level, outside any repository:

- creating GitHub Apps and their private keys;
- personal access tokens and their rotation, including this root's;
- 2FA, the profile, billing and the plan;
- account defaults for new repositories.

## The token

The root reads one value, `github_token`, from `secrets.sops.yaml` (template:
`secrets.sops.yaml.example`). The `.sops.yaml` rule for this root includes
the CI age key, so the self-hosted runner can decrypt it. The token is
(redacted) and never goes in a doc or a log.

The owner has to choose the token type. The trade-off:

| | Classic PAT | Fine-grained PAT |
| --- | --- | --- |
| Scopes or permissions for this phase | `repo` | Repository access: all repositories. Administration: read and write. Variables: read and write. Actions: read. Metadata: read |
| Can it delete a repository? | No, without the `delete_repo` scope | Yes: deleting a repository needs Administration write, which this root needs too |
| Reach | Read and write to the code of every repository too | Only the permissions listed |

A classic PAT without `delete_repo` is the only one that can't delete a
repository whatever the configuration says. A fine-grained PAT has a
smaller reach otherwise. With a classic PAT, never grant `delete_repo`.
Either way, note the expiry and rotate before it.

What the GitHub REST docs confirm: the classic `repo` scope for the Actions
permissions, workflow permissions, variables, environments and deployment
branch policy endpoints, and `delete_repo` for deleting a repository; the
fine-grained permissions in the table, from GitHub's list of permissions per
endpoint. Unconfirmed: the docs name no classic scope for updating a
repository, its rulesets, topics or Dependabot alerts (`repo` is expected);
and whether the provider calls anything else at startup that needs another
scope. The verification plan ran with a token holding `repo`, `workflow`,
`read:org` and others, so it doesn't settle either.

## One-time bootstrap

In this order:

1. **Create the token** (above) and add it to the root:
   `cp secrets.sops.yaml.example secrets.sops.yaml`, set `github_token`,
   `sops -e -i secrets.sops.yaml`. Commit only the encrypted file.
2. **Create the workspace.** Apply `environments/bootstrap/aws-init` by hand
   (README in `infrastructure/terraform/`). It adds `tnwks-github-prod`
   (execution mode local, `modules/tfc/workspaces.tf`).
3. **Check locally**: `task github:init` and `task github:plan`. The plan
   imports every resource in `imports.tf` (31) and adds and destroys
   nothing. It also shows each repository as changed in place, with only
   `archive_on_destroy` set to true: Terraform keeps that value in its state
   and GitHub has no such setting, so an import can't fill it in. Applying
   it sends each repository's current settings back unchanged.
4. **Turn CI on**: set the repository variable
   `GITHUB_TERRAFORM_ENABLED=true` (Settings > Secrets and variables >
   Actions > Variables).
5. **First apply.** Open the PR with the encrypted token. Its plan comment
   should match step 3. Merging applies it (`deploy.yaml`), or run
   `task github:apply`. After that apply, `imports.tf` can go.

## Safety

- **No deletes.** Every repository has `lifecycle { prevent_destroy = true }`,
  so a plan that would destroy one fails. `archive_on_destroy = true` means
  that, should the guard be lifted, a destroy archives the repository
  instead of deleting it.
- **No replacements.** No repository sets `template`, `fork` or `source_*`:
  on an existing repository they force a replacement, which is a delete.
- **A token that can't delete repositories** (a classic PAT without
  `delete_repo`) is the last line, independent of the code.
- **Public logs.** CI prints only counts and addresses of a plan
  ([`../network/ci.md`](../network/ci.md)), and no secret is managed here.

## Caveat: the ruleset's unattributed changes setting

The live `main` ruleset's pull request rule has
`require_extra_approval_for_unattributed_changes: true`. Provider v6.13.0
has no argument for it. The import reads the ruleset and changes nothing,
but an apply that updates the ruleset sends the rule without the field, and
GitHub may reset it to false. After any apply that changes the ruleset,
check the setting in Settings > Rules > Rulesets > `main`, and set it again
by hand if it was reset.

## The root gates its own apply

This root manages the `main` ruleset (whose required checks include the
plan's own Build Success) and the `prod` environment that the apply job
runs in. A change that breaks either can stop CI from applying the fix, for
example a `prod` environment that no longer accepts `main`, or a required
check that never reports. The way out is the break-glass task from the
desktop, against the same TFC state: `task github:plan`, then
`task github:apply` with the fix. Change these two resources in their own
small PRs.

## Later phases

- Actions, environment and Dependabot secrets, and the webhook, from SOPS.
- The other private repositories, once the owner decides whether their
  names can be in this public repo.
- The account's SSH keys.
- GitHub App installations and their repository access.
- Applying the `tnwks-ops` standard (rulesets, squash only, branch
  deletion, Actions permissions) to the other repositories.
