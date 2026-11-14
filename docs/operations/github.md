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
- **`TERRAFORM_GITHUB_ENABLED`.** It switches this root on in CI, so it
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

**On failure, one can, without the mask below.** With `-no-color`, Terraform prints an error's
summary on the `Error:` line, and for an API error the provider's summary
is the request: `Error: GET https://api.github.com/repos/iT3E/<name>: 502
...`. Any error reading or changing a repository held in SOPS (rate limit,
GitHub outage, timeout, a 403, a failed PATCH) puts its name in the public
log. Two failures don't: a bad or expired token fails on
`GET .../orgs/iT3E` before any repository, and importing a repository that
doesn't exist reports `Cannot import non-existent remote object` without
the ID. The `tail -5` fallback (output with no `Error` line, such as a
provider crash) could show progress lines too.

### The mask

Before Terraform runs, `terraform.yaml`'s plan and apply jobs mask every
value of the SOPS files that the root's entry in `.github/ci/domains.toml`
lists in `mask_sops`. For this root that is `repositories.sops.yaml` (the
names and descriptions) and `secrets.sops.yaml` (the token). The step
("Mask SOPS values") downloads a pinned sops release, checks its SHA-256,
and runs `.github/scripts/sops-mask.py`, which decrypts each file with the
runner's CI age key and prints `::add-mask::<line>` for each non-blank line
of each string value, and nothing else. From then on the runner shows `***`
for those strings anywhere in the job's log, and also for its encoded forms
(JSON, URL, XML and base64 escapes).

It fails closed: if the download or checksum fails, a file is missing or
doesn't decrypt, or no value comes out, the step fails and Terraform is
skipped. Its errors name the file, never a value or sops's own output. It
runs only for a root with `mask_sops` that is switched on (`enabled_var`).
The workflow conformance check fails a `terraform.yaml` whose Terraform
jobs lose the step or run it after Terraform, and `task github:enable`
refuses to switch the root on until `main` has it.

What it can't do:

- **Only exact strings.** A value that reaches the log changed (in another
  case, cut short, or broken over two lines) is not masked. GitHub API URLs
  use the repository name as it is, so the known leak path (the `Error:`
  line) is covered.
- **Only this job's log.** The PR comment and the job summary carry the
  plan summary, which has only addresses with neutral keys. Anything a later
  change sends elsewhere (an artifact, a comment with error text) is not
  masked.
- **Nothing printed before it.** It runs after checkout and before
  Terraform; the checkout prints no values.
- **New values need a commit.** A repository added to
  `repositories.sops.yaml` is masked from the commit that adds it, because
  the step reads the file from the same checkout.
- **Proven only in CI.** Locally the script is tested against a stub sops
  and run on the real `repositories.sops.yaml` with only its output lines
  counted. That the runner masks the values in a real failed run can only
  be seen there.

## One-time bootstrap

Two commands, from the repo root on the workstation (direnv loaded, so
`bin/sops` and the age key work). Both take `-- --dry-run`, which prints
each step without taking it.

1. **`task github:bootstrap`** (`bin/github-terraform bootstrap`). It stops
   at the first thing that is wrong, and can be run again: each step
   checks before it acts.
   1. **Token.** Skipped if `secrets.sops.yaml` is already encrypted.
      Otherwise it prints the GitHub page for a new classic token (scope
      `repo`, the description filled in) and reads the token without echo.
      It checks it with `GET /user`: the login must be `iT3E`, the scopes
      must include `repo` and must not include `delete_repo`. A
      fine-grained token has no scopes to read and can delete repositories,
      so it asks you to type `I understand`. Then it writes the file
      (umask 077), encrypts it with `bin/sops`, and checks that every value
      is encrypted, for every recipient of the `.sops.yaml` rule (CI's age
      key among them), and that it decrypts back to the token. On any
      failure, or Ctrl-C, it removes the file. The token is never on a
      command line, in an environment variable or on screen.
   2. **Workspace.** Skipped if `tnwks-github-prod` exists with the
      settings in `modules/tfc/workspaces.tf` (it checks with a GET, and
      stops if they differ). Otherwise, after you type `yes`, it creates
      the workspace through the Terraform Cloud API (project `AWS_Project`,
      working directory `environments/prod/github`, execution mode local)
      and adds the two import blocks that adopt it to
      `environments/bootstrap/aws-init/imports.tf`. That is how the
      mikrotik, unifi, healthchecks and cloudflare workspaces were added:
      `aws-init` itself can't be applied today (its state predates its
      `moved` blocks, which reach an IAM resource its static credentials
      can't read, and a `-target` apply must include every moved resource),
      so it adopts them on its next apply.
   3. **Plan.** `terraform init` and `terraform plan` in the root. It
      passes only if the plan imports and does nothing else
      (`36 to import, 0 to add, 0 to change, 0 to destroy`), or has no
      changes, and prints that line and the imports by type, never the plan
      (which names the private repositories). If it fails, run
      `task github:plan` to see why.
   4. **What's left.** It re-checks the token file and prints the
      `git add` (the token file, and `aws-init/imports.tf` if it changed).
2. **Commit and merge** what it printed: a branch, a Changie fragment, a PR.
   `TERRAFORM_GITHUB_ENABLED` is still unset, so CI skips this root on that
   PR and its merge.
3. **`task github:enable`** (`bin/github-terraform enable`). It fetches
   `origin/main` and refuses unless `main` has the encrypted token (for the
   rule's recipients), the root's `mask_sops` with both files, the mask
   script, and the mask step before Terraform in both jobs. Then it sets
   `TERRAFORM_GITHUB_ENABLED=true` (`env -u GITHUB_TOKEN gh variable set`)
   and reads it back. Nothing else turns this root on in CI.
4. **First apply.** The next merge that touches the root applies it, or
   dispatch it now: `env -u GITHUB_TOKEN gh workflow run deploy.yaml
   --repo iT3E/tnwks-ops -f roots=github`. The apply imports and changes
   nothing (step 1.3 checked that locally; CI's PR comment counts only
   changes, so it shows `0 to add, 0 to change, 0 to destroy, 0 to move`
   for an imports-only plan). After it, `imports.tf` can go.

What stays your decision: the token type and its expiry (a fine-grained
token can delete repositories), confirming the workspace, reviewing the PR,
and when to turn CI on.

## Safety

- **No deletes.** Every repository has `lifecycle { prevent_destroy = true }`,
  so a plan that would destroy one fails. Should the guard ever be lifted,
  `archive_on_destroy = true` archives the repository instead of deleting
  it. GitHub never reports that setting, so it lives only in Terraform's
  state: an import can't fill it in, and a repository imported later plans
  as an import plus an in-place update that records it. That update
  resends the repository's settings as Terraform read them
  (`PATCH /repos/{owner}/{repo}`; visibility and secret scanning only when
  they change).
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

- Actions, environment and Dependabot secrets, and the webhook, from SOPS.
- The account's SSH keys.
- GitHub App installations and their repository access.
- Applying the `tnwks-ops` standard (rulesets, squash only, branch
  deletion, Actions permissions) to the other repositories.
