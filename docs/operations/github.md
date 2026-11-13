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
| `github_repository` | every repository of the account: public ones and private ones named in `main.tf`, and private ones held in SOPS (below) | Description, homepage, visibility, topics, features (issues, wiki, projects, discussions), merge methods and commit messages, auto-merge, branch deletion and update, sign-off, template, archived. Secret scanning and push protection on public repositories only |
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

### Private repositories held in SOPS

A private repository is named in `main.tf` only if its name is already
public elsewhere in this repo (outside the paths `.github/privacy-allow`
exempts). The rest are listed under neutral keys, `private1`, `private2`
and so on, in `local.sops_repositories` in `main.tf`, with their
non-identifying settings (visibility, wiki, merge settings) in plain text.
Their names and descriptions are encrypted values under the same keys in
`repositories.sops.yaml` in the root:

```yaml
private3:
  name: (redacted)
  description: (redacted)   # left out when empty: sops leaves "" unencrypted
```

The key is the resource address (`module.github.github_repository.this["private3"]`),
so it shows in plans and in CI. Never rename or reuse a key; a new
repository takes the next free number. The real name is in Terraform Cloud's
state and in a local plan's output, never in the code, a PR or CI's output
(see Public logs for the one exception).

To look one up (from the repo root, which `bin/sops` needs to find the
`.sops.yaml` rules):

```bash
bin/sops -d --extract '["private3"]["name"]' infrastructure/terraform/environments/prod/github/repositories.sops.yaml
```

To add one:

1. `bin/sops infrastructure/terraform/environments/prod/github/repositories.sops.yaml`
   opens the decrypted file in your editor and encrypts it on save. Add
   `privateN` with `name` and, if the repository has one, `description`.
2. Add `privateN` to `local.sops_repositories` in `main.tf` with its
   settings. While the repository exists on GitHub and not in the state,
   it also needs an import block (the `sops_repositories` one in
   `imports.tf` covers every key).
3. Check that the file is encrypted (`sops` metadata, every value
   `ENC[...]`) before `git add`, and run `task github:plan`.

Refer to the repository only by its key in commit messages, PR titles and
bodies, comments and reviews. The privacy checks don't know these names, so
nothing stops one from being pasted there.

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
the CI age key, so the self-hosted runner can decrypt it and
`repositories.sops.yaml`. The token is (redacted) and never goes in a doc
or a log.

The owner has to choose the token type. What this phase needs:

| | Classic PAT | Fine-grained PAT |
| --- | --- | --- |
| Scopes or permissions | `repo` | Repository access: all repositories. Administration: read and write. Variables: read and write. Actions: read. Metadata: read |
| Not needed | `workflow` (it pushes workflow files; this root writes no files), `delete_repo` | Environments (it covers environment secrets and variables, which this phase doesn't manage) |
| Can it delete a repository? | No, without `delete_repo` | Yes: deleting a repository needs Administration write, which this root needs too |
| Reach | Read and write to the code of every repository too | Only the permissions listed |

A classic PAT without `delete_repo` is the only one that can't delete a
repository whatever the configuration says. A fine-grained PAT has a
smaller reach otherwise. Either way, note the expiry and rotate before it.

Confirmed in GitHub's REST docs: the classic `repo` scope for the Actions
permissions, workflow permissions, Actions variables, environments and
deployment branch policy endpoints; `delete_repo` for deleting a
repository; and every fine-grained permission in the table, from GitHub's
list of permissions per endpoint (environments and branch policies are read
with Actions and written with Administration).

Unconfirmed: the docs name no classic scope for updating a repository, its
rulesets, topics or Dependabot alerts (`repo` is expected); and whether the
provider calls anything at startup that needs more. The verification plan
ran with a token holding `repo`, `workflow`, `read:org` and others, so it
doesn't settle either. If the first plan fails on a permission, the error
names the endpoint.

## Public logs

The repo is public, and so are its Actions logs. CI runs this root through
github-workflows' `actions/terraform` (pinned in `terraform.yaml`). What it
prints:

| Step | On success | On failure |
| --- | --- | --- |
| Init | nothing | lines starting with `│` or `Error` from `terraform init`, else its last 5 lines |
| Plan | the summary: counts, then one line per changing address (``- `update` module.github.github_repository.this["private3"]``). The same text goes to the PR comment and the job summary | lines starting with `│` or `Error` from `terraform plan`, else its last 5 lines. No PR comment |
| Apply | the `Apply complete!` line | as for the plan, from `terraform apply` |

The plan body, Terraform's progress lines (which print each repository's
ID, its name: `Refreshing state... [id=...]`) and the state never reach
the log. Addresses use the neutral keys. On success, no name of a
repository held in SOPS reaches a public log or the PR.

**On failure, one can.** With `-no-color`, Terraform prints an error's
summary on the `Error:` line, and for an API error the provider's summary
is the request: `Error: GET https://api.github.com/repos/iT3E/<name>: 502
...`. Any error reading or changing a repository held in SOPS (rate limit,
GitHub outage, timeout, a 403, a failed PATCH) puts its name in the public
log. Two failures don't: a bad or expired token fails on
`GET .../orgs/iT3E` before any repository, and importing a repository that
doesn't exist reports `Cannot import non-existent remote object` without
the ID. The `tail -5` fallback (output with no `Error` line, such as a
provider crash) could show progress lines too.

Proposed mitigation, before `GITHUB_TERRAFORM_ENABLED` is set: a step in
`terraform.yaml`, for the `github` root only and before the plan or apply,
that decrypts `repositories.sops.yaml` with the runner's CI age key and
prints `::add-mask::<value>` for every name and description. GitHub then
shows `***` for them anywhere in the job's log. Whether `sops` is on the
runner image is unconfirmed; the step may need to install it. A narrower
alternative belongs in github-workflows: an input to the action that masks
or drops error lines matching a pattern, at the cost of less useful errors.

## One-time bootstrap

In this order:

1. **Create the token** (above) and add it to the root, from the repo root:
   `cp infrastructure/terraform/environments/prod/github/secrets.sops.yaml.example infrastructure/terraform/environments/prod/github/secrets.sops.yaml`,
   set `github_token`, then
   `bin/sops -e -i infrastructure/terraform/environments/prod/github/secrets.sops.yaml`.
   Commit only the encrypted file.
2. **Create the workspace.** Apply `environments/bootstrap/aws-init` by hand
   (README in `infrastructure/terraform/`). It adds `tnwks-github-prod`
   (execution mode local, `modules/tfc/workspaces.tf`).
3. **Check locally**: `task github:init` and `task github:plan`. The plan
   imports every resource in `imports.tf` (36) and adds, changes and
   destroys nothing.
4. **Mask the names** in CI's log (Public logs, the proposed step).
5. **Turn CI on**: set the repository variable
   `GITHUB_TERRAFORM_ENABLED=true` (Settings > Secrets and variables >
   Actions > Variables).
6. **First apply.** Open the PR with the encrypted token. Its plan comment
   reads `0 to add, 0 to change, 0 to destroy, 0 to move` with no
   addresses: the action counts only changes, not imports, so check the
   imports in step 3's local plan. The plan still reports changes
   (`-detailed-exitcode` 2), so merging applies it (`deploy.yaml`), or run
   `task github:apply`. After that apply, `imports.tf` can go.

## Safety

- **No deletes.** Every repository has `lifecycle { prevent_destroy = true }`,
  so a plan that would destroy one fails. `archive_on_destroy = true`
  (archive instead of delete, should the guard be lifted) is not set in
  this phase; see "Later phases".
- **No replacements.** No repository sets `template`, `fork` or `source_*`:
  on an existing repository they force a replacement, which is a delete.
- **A token that can't delete repositories** (a classic PAT without
  `delete_repo`) is the last line, independent of the code.
- **Public logs**: see above. No secret is managed here.

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

- `archive_on_destroy = true` on every repository, in its own PR after the
  first apply. GitHub never reports the setting, so an import can't fill it
  in: the plan shows it as one in-place update per repository, and the apply
  sends each repository's current settings back unchanged
  (`PATCH /repos/{owner}/{repo}`) while Terraform records the value in its
  state.
- The log mask for the repositories held in SOPS (Public logs), if it isn't
  in place before CI is turned on.
- Actions, environment and Dependabot secrets, and the webhook, from SOPS.
- The account's SSH keys.
- GitHub App installations and their repository access.
- Applying the `tnwks-ops` standard (rulesets, squash only, branch
  deletion, Actions permissions) to the other repositories.
