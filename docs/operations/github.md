# GitHub account (Terraform)

The `iT3E` account is a personal GitHub account with no organizations. Its
repositories and the settings that gate this repo (the `main` ruleset, the
deployment environments, the Actions settings) are Terraform like the rest
of the infrastructure: `infrastructure/terraform/environments/prod/github`
holds the values, `infrastructure/terraform/modules/github` the resources.

This first phase only codifies what exists. Every value was read from the
API and was imported as it was, including settings that differ between
repositories for no reason. Making them consistent is a later change, one
PR at a time, each with its own plan.

## What it manages

| Resource | Where | Notes |
| --- | --- | --- |
| `github_repository` | every repository of the account: public ones and private ones named in `main.tf`, and private ones held in SOPS (below) | Description, homepage, visibility, topics, features (issues, wiki, projects, discussions), merge methods and commit messages, auto-merge, branch deletion and update, sign-off, template, archived. Secret scanning and push protection on public repositories only |
| `github_repository_ruleset` | `tnwks-ops`: `main` | Deletion and force-push blocked, PRs with squash merges only and no approvals, the required checks `Build Success`, `betterleaks / betterleaks` and `privacy / privacy` from GitHub Actions. Admins and one GitHub App bypass it |
| `github_repository_environment` | `tnwks-ops`: `external-contributions`, `openclaw-hooks`, `prod` | The owner as required reviewer of `external-contributions`; `openclaw-hooks` deploys from `main` only; `prod` from protected branches only |
| `github_repository_environment_deployment_policy` | `tnwks-ops`: `openclaw-hooks` | The `main` branch pattern |
| `github_actions_variable` | `tnwks-ops`: `RELEASE_APP_CLIENT_ID`, `UNIFI_TERRAFORM_ENABLED` | Values are public. `TERRAFORM_GITHUB_ENABLED` stays by hand (below) |
| `github_actions_environment_variable` | `tnwks-ops`, `openclaw-hooks`: `OPENCLAW_NOTIFY_ACCOUNT`, `OPENCLAW_NOTIFY_CHANNEL`, `OPENCLAW_NOTIFY_TO` | Values from `tnwks-ops.sops.yaml` (below) |
| `github_repository_webhook` | `tnwks-ops`: `flux` | Flux's receiver: `push` events, form content type, URL from `tnwks-ops.sops.yaml`. Its secret isn't managed, and Terraform never updates the webhook (below) |
| `github_actions_repository_permissions` | `tnwks-ops` | Actions on, all actions allowed |
| `github_workflow_repository_permissions` | `tnwks-ops` | `GITHUB_TOKEN` read-only by default, can't approve PRs |
| `github_repository_vulnerability_alerts` | `tnwks-ops` | Dependabot alerts on |

The repositories come from one map in `main.tf`. Rulesets, environments
(with their variables), webhooks, Actions settings, variables and
Dependabot alerts are optional parts of each entry, so applying them to
another repository is a data change. An entry lists only what differs from
a new repository's defaults (`modules/github/variables.tf`).

### The webhook's secret

GitHub never returns a webhook's secret: it reads back as `********`, and
an import stores that in the state. The `flux` webhook's secret belongs to
the Flux receiver in the cluster (`github-webhook-token-secret`), so this
root doesn't hold it. The provider sends the whole configuration, secret
included, on any update of a webhook, and GitHub removes or replaces the
secret unless the update carries the same one. So the module ignores every
argument an update would send (`ignore_changes` on `configuration`,
`events` and `active`, in `modules/github/webhooks.tf`): Terraform adopts or
creates a webhook and never updates it. It still plans a create if the
webhook disappears (a new one without a secret, which the receiver would
reject: set the secret on GitHub after such an apply).

To change the webhook (events, URL, active), change it on GitHub and update
the entry (and `tnwks-ops.sops.yaml` for the URL) to match; the plan shows
no change either way. Webhooks of other repositories would get the same
treatment.

On this account's plan the API refuses rulesets, branch protection and
security and analysis settings for private repositories ("Upgrade to GitHub
Pro"), so those entries have none. The module rejects them.

### Values held in SOPS

Three files in the root hold values that stay out of this public repo:

- `repositories.sops.yaml`: the names and descriptions of private
  repositories (this section);
- `tnwks-ops.sops.yaml`: the `flux` webhook's URL (its host is under the
  cluster's `SECRET_DOMAIN`) and two of the `openclaw-hooks` environment's
  variable values, which may identify a person (next section);
- `unmasked.sops.yaml`: values too short to mask, today
  `OPENCLAW_NOTIFY_ACCOUNT` (next section).

The first two (and `secrets.sops.yaml`, the token) are masked in CI's log;
`unmasked.sops.yaml` is not, on purpose (The mask, below). All of them are
encrypted in git the same way.

#### Private repositories

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
   settings. If the repository already exists on GitHub, it also needs an
   import block (below).
3. Check that the file is encrypted (`sops` metadata, every value
   `ENC[...]`) before `git add`, and run `task github:plan`.

Refer to the repository only by its key in commit messages, PR titles and
bodies, comments and reviews. The privacy checks don't know these names, so
nothing stops one from being pasted there.

#### tnwks-ops' webhook URL and environment variables

`tnwks-ops.sops.yaml` and `unmasked.sops.yaml` follow the shape of the
`tnwks-ops` entry; the names are in `main.tf` (`webhooks.flux`,
`local.openclaw_hooks_variables`, which says which file each variable is
in):

```yaml
# tnwks-ops.sops.yaml
webhooks:
  flux:
    url: (redacted)
environments:
  openclaw-hooks:
    variables:
      OPENCLAW_NOTIFY_CHANNEL: (redacted)
      OPENCLAW_NOTIFY_TO: (redacted)
---
# unmasked.sops.yaml
environments:
  openclaw-hooks:
    variables:
      OPENCLAW_NOTIFY_ACCOUNT: (redacted)
```

To change a value, `bin/sops infrastructure/terraform/environments/prod/github/<file>`
from the repo root, then check it as in step 3 above and run
`task github:plan`: a variable shows as an in-place update, which the apply
sends to GitHub. A new variable needs its name in
`local.openclaw_hooks_variables` and its value in one of the two files: in
`tnwks-ops.sops.yaml` if it is at least 6 characters long (each line, for a
multi-line value), else in `unmasked.sops.yaml`; CI's mask step fails on a
shorter value in a masked file (The mask). sops leaves an empty string
unencrypted, so an empty value doesn't go in either file. The root
passes the values through `nonsensitive()`, as it does the repository
names: GitHub returns them unmarked, so a marked value would plan as an
update of each variable. Locally, `task github:plan` prints them when they
change; CI prints no plan body. The webhook URL stays marked (the provider
marks it sensitive itself), and changing it here changes nothing on GitHub
(The webhook's secret, above).

### Adopting something that already exists

The first apply adopted everything the root lists with import blocks, and a
later one the `flux` webhook and the `openclaw-hooks` variables; the blocks
were removed after each. Anything added to `main.tf` later that already exists on
GitHub (a repository, an environment, a variable, a ruleset) needs an import
block in the root first, or the apply fails on "already exists". Put it in
`environments/prod/github/imports.tf` and take the file out again in a later
PR, once an apply has imported it. The plan should import it and change
nothing; if it also shows an update, the values in `main.tf` differ from
GitHub's.

```hcl
import {
  to = module.github.github_repository.this["some-repo"]
  id = "some-repo"
}

# A repository held in SOPS: the import ID is its name, and an import ID
# can't be sensitive, so nonsensitive() here, and only here.
import {
  to = module.github.github_repository.this["private6"]
  id = nonsensitive(data.sops_file.repositories.data["private6.name"])
}
```

Other IDs: `<repo>:<ruleset id>` for a ruleset,
`<repo>:<environment>` for an environment,
`<repo>:<environment>:<policy id>` for a deployment policy,
`<repo>:<variable>` for a variable,
`<repo>:<environment>:<variable>` for an environment's variable,
`<repo>/<hook id>` for a webhook, and the repository name for the
Actions settings and Dependabot alerts. Terraform prints import IDs in its
progress lines, which CI keeps out of the log (Public logs).

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
- **Secrets whose values only the owner holds**: `TF_API_TOKEN` (on the
  repository and on `external-contributions`), `RELEASE_APP_PRIVATE_KEY`
  and `OPENCLAW_HOOK_TOKEN`. Not even their existence is managed: a secret
  resource that is later removed from the code deletes the secret, and CI
  depends on each of them. Managing their values would mean copying them
  into SOPS here.
- **`PRIVACY_TERMS`**, for now: a separate change, waiting on a decision.
- **Dependabot secrets**, and Actions or environment secrets of other
  repositories.
- **The `flux` webhook's secret.** It belongs to the Flux receiver in the
  cluster; the webhook itself is adopted, but never updated (The webhook's
  secret, above).

Account-level, outside any repository:

- creating GitHub Apps and their private keys;
- personal access tokens and their rotation, including this root's;
- 2FA, the profile, billing and the plan;
- account defaults for new repositories.

## The token

The root reads one value, `github_token`, from `secrets.sops.yaml` (template:
`secrets.sops.yaml.example`). The `.sops.yaml` rule for this root includes
the CI age key, so the self-hosted runner can decrypt it and the other
SOPS files of the root. The token is (redacted) and never goes in a doc or
a log.

The owner has to choose the token type. What this phase needs:

| | Classic PAT | Fine-grained PAT |
| --- | --- | --- |
| Scopes or permissions | `repo` | Repository access: all repositories. Administration: read and write. Variables: read and write. Environments: read and write. Webhooks: read and write. Actions: read. Metadata: read |
| Not needed | `workflow` (it pushes workflow files; this root writes no files), `delete_repo` | Secrets (this root manages none) |
| Can it delete a repository? | No, without `delete_repo` | Yes: deleting a repository needs Administration write, which this root needs too |
| Reach | Read and write to the code of every repository too | Only the permissions listed |

A classic PAT without `delete_repo` is the only one that can't delete a
repository whatever the configuration says. A fine-grained PAT has a
smaller reach otherwise. Either way, note the expiry and rotate before it.

Confirmed in GitHub's REST docs: the classic `repo` scope for the Actions
permissions, workflow permissions, Actions variables, environment
variables, environments and deployment branch policy endpoints; `delete_repo`
for deleting a repository; and every fine-grained permission in the table,
from GitHub's list of permissions per endpoint (environments and branch
policies are read with Actions and written with Administration; environment
variables need Environments, webhooks need Webhooks).

Unconfirmed: the docs name no classic scope for updating a repository, its
rulesets, topics or Dependabot alerts (`repo` is expected), nor for the
webhook endpoints themselves (they name `repo` or the `*:repo_hook` scopes
only for a webhook's configuration endpoints; the root's token read the
webhook in the plan that adopted it, and Terraform never writes to it unless
it has to create one); and whether the
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
repository held in SOPS reaches a public log or the PR. The values in
`tnwks-ops.sops.yaml` and `unmasked.sops.yaml` are in no address or ID
either: an environment variable's ID is `tnwks-ops:openclaw-hooks:<name>`,
the webhook's its number. An API error about an environment variable is the
request line and GitHub's message: the URL names the variable, and the value
is only in the request body, which the error doesn't print.

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
names and descriptions), `secrets.sops.yaml` (the token) and
`tnwks-ops.sops.yaml` (the webhook URL and two environment variables'
values). The step
("Mask SOPS values") downloads a pinned sops release, checks its SHA-256,
and runs `.github/scripts/sops-mask.py`, which decrypts each file with the
runner's CI age key and prints `::add-mask::<line>` for each non-blank line
of each string value, and nothing else. From then on the runner shows `***`
for those strings anywhere in the job's log, also inside longer words, and
also for their encoded forms (JSON, URL, XML and base64 escapes). The runner
masks a string of any length (actions/runner's `SecretMasker.AddValue` has
no minimum).

That is why a short value can't be masked safely. A value of a few
characters occurs inside ordinary words, paths and IDs, so the log shows
`***` in the middle of them, often next to the same text printed unmasked
before the mask step, and the value can be read off. So `sops-mask.py`
refuses a line shorter than 6 characters: the step fails,
naming the file and key path. A value that short goes in
`unmasked.sops.yaml`, which is not in `mask_sops`: CI decrypts it for
Terraform but doesn't mask it, so it reaches the log only if Terraform
prints it, which it doesn't on success (Public logs). 6 is a judgment, not a
runner limit; a longer value that is a common word has the same problem, and
no check can see that.

It fails closed: if the download or checksum fails, a file is missing or
doesn't decrypt, no value comes out, or a value is too short, the step
fails and Terraform is skipped. Its errors name the file (and, for a short
value, its key path), never a value or sops's own output. It runs only for a
root with `mask_sops` that is switched on (`enabled_var`).
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
- **Nothing printed before it.** Masks aren't retroactive. It runs after
  checkout and before Terraform; the checkout prints no values, only text
  that a short value could occur in (above).
- **Not `unmasked.sops.yaml`.** Its values are protected by nothing but
  Terraform not printing them.
- **New values need a commit.** A repository added to
  `repositories.sops.yaml`, or a value added to `tnwks-ops.sops.yaml`, is
  masked from the commit that adds it, because the step reads the file from
  the same checkout.
- **Proven in CI only for what has happened.** Locally the script is tested
  against a stub sops and run on the real files with only its output lines
  counted. Real job logs show the runner masking values after the step,
  inside longer words too; that it masks a value in a real `Error:` line
  has not been seen yet.

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
   for an imports-only plan). It ran on 2026-11-13, imported all 36
   objects and changed nothing, and the import blocks were removed after
   it.

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
- **No webhook updates.** Terraform never updates a webhook, so it can't
  remove the `flux` webhook's secret (The webhook's secret, above).

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

- `PRIVACY_TERMS`, once its owner decision is made.
- Secrets whose values Terraform could hold in SOPS here. The owner-held
  secrets above stay by hand unless that changes, and never existence-only.
- Updating the `flux` webhook from Terraform: only with its secret in this
  root, from the same source as the Flux receiver's, so that an update
  carries the same secret.
- The account's SSH keys.
- GitHub App installations and their repository access.
- Applying the `tnwks-ops` standard (rulesets, squash only, branch
  deletion, Actions permissions) to the other repositories.
