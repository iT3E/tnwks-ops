# App repos

The repos that hold application source and are deployed by this one get the
same repo setup as tnwks-ops: CI on every PR, Renovate, pre-commit hooks,
Changie releases, labels, and secret and privacy scanning. tnwks-ops is the
reference. When a workflow, preset or `bin/privacy-check` changes here, port
it to the app repos too.

| Repo | Language | Image | tnwks-ops pins |
| --- | --- | --- | --- |
| `iT3E/tnwks-packing-api` | Go | `ghcr.io/it3e/tnwks-packing-api` | `:main` (`kubernetes/apps/production/packing-api`) |
| the storefront API | Go | `ghcr.io/it3e/<repo>` | `:main` (`kubernetes/apps/production/*-things-api`) |
| `iT3E/tnwks-finance-sync` | Python | `ghcr.io/it3e/tnwks-finance-sync` | `:<version>@sha256` (`kubernetes/apps/production/finance-sync`) |

All three are private on GitHub Free, and their images are public in GHCR.
Both facts shape what follows: the private repos have no rulesets and
metered Actions minutes, and the public images mean the privacy rules apply
as strictly as they do here.

## What every app repo has

| File | What it does | tnwks-ops counterpart |
| --- | --- | --- |
| `.github/workflows/build.yaml` | Lint, test, Docker build on PRs; push the image from `main` and/or `v*` tags | `kubernetes.yaml` (Build Success) |
| `.github/workflows/actionlint.yaml` | actionlint (with shellcheck) when a PR touches workflows | none; `actionlint.yaml` config only |
| `.github/workflows/secret-scan.yaml` | betterleaks over the commits a PR (or a push to `main`) adds | same |
| `.github/workflows/privacy.yaml` | `bin/privacy-check` on the tree, the PR's commits and identities, and the PR title and body | same |
| `.github/workflows/privacy-text.yaml` | the same check on comments, reviews and issues; deletes a comment with a hit | same, copied as is |
| `.github/workflows/changelog.yaml` | a Changie fragment, unless labeled `skip-changelog` or opened by a bot | same, without the Home Assistant carve-out |
| `.github/workflows/release.yaml` | Changie batch, release commit, `vX.Y.Z` tag, GitHub Release, image build for the tag | same model; see [Releases](#releases) |
| `.github/workflows/meta-labeler.yaml`, `.github/labeler.yaml` | `area/*` labels from the changed paths | same |
| `.github/workflows/meta-sync-labels.yaml`, `.github/labels.yaml` | labels synced from the file (others deleted) | same |
| `.github/renovate.json5` | Renovate, extending the shared presets here | `.github/renovate.json5` |
| `.github/privacy-allow` | file exceptions for the privacy check, only where the owner decided one | same |
| `.pre-commit-config.yaml` | betterleaks, privacy-check (staged files, git identity, commit message), the language formatter | same, plus the formatter |
| `.betterleaks.toml` | upstream rules plus the prose-credential rule | same, without the vendored-code allowlist |
| `.editorconfig`, `.gitattributes` | LF, UTF-8, two spaces; tabs for Go, four spaces for Python | same plus language sections; no SOPS diff driver |
| `.changie.yaml`, `.changes/`, `CHANGELOG.md` | changelog fragments and released versions | same |
| `Taskfile.yml` | `precommit:init`, `precommit:run`, `privacy:check`, `privacy:sync`, `changelog:new`, plus `test`/`lint` | `.taskfiles/PrecommitTasks.yml`, `PrivacyTasks.yml` |
| `bin/privacy-check` | the denylist check | same matching; terms from a tnwks-ops checkout instead of a local SOPS file |
| `bin/install-hooks` | `pre-commit install --install-hooks`, then checks the denylist loads | `task precommit:init` |
| `AGENTS.md`, README "Workflow" | how to work in the repo, the privacy rules, no emdashes in prose | `AGENTS.md` |

finance-sync doesn't have `AGENTS.md`, the README section, `bin/install-hooks`
or the `bin/privacy-check` fallback yet. They were held back so they wouldn't
collide with its Postgres port. Until that follow-up lands, its pre-commit
config loads the denylist itself.

## CI on pull requests

Every check below must pass before a merge. `gh-merge-safe.sh` waits for all
of them, and Renovate's `ignoreTests: false` does the same for its own PRs.

- **Go** (`build.yaml`): `gofmt -l` must be empty, then `go vet ./...`,
  golangci-lint (`.golangci.yml`, v2 `standard` linters; on PRs and only for
  issues the PR introduces, so existing findings don't block), `go test -race
  ./...`, and a Docker build without push. CI's Go version comes from
  `go.mod` (`setup-go` `go-version-file`).
- **Python** (finance-sync `build.yaml`): what the repo already ran. Build
  the image, smoke-test the commands, run the unit tests inside it (synthetic
  data only), and check the image for state or secret files
  (`bin/image-data-guard`). No ruff in CI until the tree is formatted.
- **Both**: `actionlint`, `betterleaks`, `privacy`, `changelog`, `Labeler`.

The PR privacy check is `privacy.yaml`. Before an image is pushed, the build
re-runs the check on the exact tree: Go repos on every push, finance-sync on
tags over the whole history.

## Releases

Same model as here: each PR adds a Changie fragment (or carries
`skip-changelog`), and merging fragments to `main` runs `release.yaml`. It
batches them into `.changes/<version>.md` and `CHANGELOG.md`, pushes a
`chore(release): vX.Y.Z` commit, tags it, and publishes a GitHub Release.
Fixed/Security bump the patch, Added/Changed/Deprecated/Removed the minor,
Breaking the major.

Two differences, both from GitHub Free:

- **No release app.** With no rulesets on these repos, `GITHUB_TOKEN`
  (`contents: write`) can push the release commit and tag.
- **The build is dispatched.** A tag pushed with `GITHUB_TOKEN` starts no
  workflow, so `release.yaml` ends with `gh workflow run build.yaml --ref
  vX.Y.Z` (`actions: write`). A tag pushed by hand still triggers
  `build.yaml` directly, but the next Changie release won't know about it,
  so tags should come from `release.yaml`.

Image tags must stay what tnwks-ops pins (`git grep ghcr.io/it3e/` here):

| Event | Go repos | finance-sync |
| --- | --- | --- |
| PR | build only (`pr-<n>` stays local) | build only |
| push to `main` | `:main`, `:sha-<short>` | build only |
| `v*` tag | `:<version>`, `:sha-<short>` | `:<version>`, `:sha-<short>` |

`metadata-action` runs with `latest=false`, so nothing pushes `:latest`. The
Go apps deploy `:main` with `imagePullPolicy: Always`, so a merge ships on
the next pod restart, and a release only adds a version tag on top. The
release commit itself triggers nothing, so `:main` stays on the merge
commit's image (same code). finance-sync's `main` pushes nothing: a version
reaches the cluster only when a tnwks-ops PR moves its `:<version>@sha256`
pin.

`release.yaml`'s `FIRST_VERSION` is `v0.1.0` and is used only while
`.changes/` has no `v*.md`. finance-sync had tags `v0.1.0` to `v0.1.3`
before Changie; they are backfilled under `.changes/`, so its next release
follows them.

## Renovate

`.github/renovate.json5` starts from the same base as tnwks-ops'
(`config:best-practices`, `docker:enableMajor`, `:semanticCommits`,
`:enablePreCommit`, `helpers:pinGitHubActionDigests`, the dashboard) and
extends three of the shared presets:

```json5
"github>iT3E/tnwks-ops//.github/renovate/commitMessage.json5",
"github>iT3E/tnwks-ops//.github/renovate/labels.json5",
"github>iT3E/tnwks-ops//.github/renovate/semanticCommits.json5",
```

`autoMerge.json5` and `groups.json5` are not extended. They are built on
tnwks-ops paths and packages (Kubernetes app directories, Flux, Talos), and
their negated-path review rule would mark every update in an app repo
`impact/high`. Each app repo carries the same tiers for its own managers
instead:

- **Automerge** once every check is green: patch and digest updates of Go
  modules / Python packages and the base images, non-0.x minors of modules
  and packages, GitHub Actions minor/patch/digest (3 days after release,
  none for `actions/*`), pre-commit hooks, and CI tools pinned with
  `# renovate:` comments.
- **Reviewed** (left open): majors, 0.x minors, and in finance-sync Python
  image minors. There is no `impact/high` hand-off to Ori's reviewer agent:
  `renovate-review.yaml` runs on the `openclaw-hooks` runner, which is
  registered to tnwks-ops only.

Differences from tnwks-ops, and why:

- `docker:pinDigests` stays on. The Dockerfile base images are pinned
  `tag@sha256` and Renovate keeps both current. (tnwks-ops turns it off
  because it would rewrite every HelmRelease.)
- `platformAutomerge: false` is required here, not a choice: GitHub Free
  has no auto-merge on private repos. Renovate merges its own PRs, squash.
- `schedule: ["before 7am on monday"]` instead of daily, because Actions
  minutes on private repos are metered (2,000 a month on Free, shared by
  every private repo). Security updates ignore the schedule.
- Labels: `renovate/go` and `renovate/python` on top of the shared ones,
  and semantic commit scopes `go` and `python`.
- Go: `gomodTidy` and `gomodUpdateImportPaths`. The go directive and the
  `golang` build image are one "Go toolchain" group.
- Python: `constraints.txt` is a `pip_requirements` file too. Pillow and
  requests are held at amazoncaptcha's caps (`<9.6`, `<2.31`).

The Renovate GitHub App (Mend) only sees the repos it is granted, and it is
installed for selected repositories (in November 2026, only tnwks-ops and
vyos-config had a dashboard). Adding a repo
is a UI-only step: <https://github.com/settings/installations>, Renovate,
Configure, Repository access, "Select repositories", add the repo, Save.
The repo already has a config, so the first run opens the "Renovate
Dashboard 🤖" issue (and a "Pin dependencies" PR if anything is unpinned)
instead of an onboarding PR. Job logs, after signing in with GitHub:
`https://developer.mend.io/github/iT3E/<repo>`.

## Hooks

`.pre-commit-config.yaml` installs pre-commit and commit-msg hooks
(`default_install_hook_types`), via `bin/install-hooks` / `task
precommit:init`:

- `betterleaks` (staged changes, `.betterleaks.toml`)
- `privacy-check` (`bin/privacy-check --cached`: staged files and your git
  identity) and `privacy-check-message` (the commit message)
- the formatter: `gofmt -l -w` as a `language: golang` hook (pre-commit
  downloads Go if it isn't installed), and `ruff-format` for Python. In
  finance-sync ruff is a manual-stage hook until the tree is formatted in a
  PR of its own.

golangci-lint runs in CI only (`task lint` runs it in Docker). It is too
slow for a hook.

## Privacy

The rules in [`AGENTS.md`](../../AGENTS.md#privacy) apply unchanged. The
denylist has one source: `.github/privacy-terms.sops.yaml` here.

- **CI** reads it from each repo's `PRIVACY_TERMS` Actions secret. Set or
  refresh it from a tnwks-ops checkout (`task privacy:sync` in the app repo
  does this) and never print it:

  ```bash
  sops -d --extract '["terms"]' .github/privacy-terms.sops.yaml \
    | gh secret set PRIVACY_TERMS --repo iT3E/<repo>
  ```

  Re-sync every app repo after `task privacy:edit` here. A missing secret
  fails the check rather than skipping it, since private repos have no fork
  PRs.
- **Locally**, the app repos' `bin/privacy-check` reads `$PRIVACY_TERMS`,
  or else decrypts the list from a tnwks-ops checkout at `../tnwks-ops`
  (or `$TNWKS_OPS_DIR`). The hooks fail closed if it can't load.
- **Text you post** (PR bodies, comments) gets the same treatment as here:
  write it to a file, run `bin/privacy-check --text < file` from the
  tnwks-ops root, and post with `--body-file`.
- **Exceptions** follow the same rule as here: the owner's call, name-free.
  The storefront is public under its own name and that name is on the
  denylist. Here the exception is its app directory. In its own repo,
  `.github/privacy-allow` lists the existing files that carry the name;
  every other file and any new one is checked in full. Commit messages,
  PR text and comments have no exceptions, so those say "the storefront".

## Merging without branch protection

Rulesets and classic branch protection need GitHub Pro on private repos.
The API answers 403 ("Upgrade to GitHub Pro or make this repository
public"). Nothing enforces review or green checks on `main`, so the
workflow does:

- Every change is a PR, squash-merged. No direct pushes to `main`, by
  people or agents.
- Merge with `~/.openclaw/scripts/gh-merge-safe.sh <pr> iT3E/<repo>`. It
  waits for every check and refuses unless all passed or were skipped.
- Renovate automerges only with every check green (`ignoreTests: false`,
  `platformAutomerge: false`).
- The only direct push is `release.yaml`'s release commit, which touches
  only `.changes/` and `CHANGELOG.md`.

If a repo goes public or the account moves to Pro, add the same `main`
ruleset as tnwks-ops, with the checks above required.

## What doesn't transfer

- SOPS (`.sops.yaml`, `bin/sops`, `*.sops.*` diff driver): app repos hold
  no secrets. Runtime secrets are SOPS Secrets in tnwks-ops.
- `kubernetes.yaml`, Terraform, MikroTik, UniFi, EdgeOS, Home Assistant and
  link-check workflows, and the self-hosted runners (`actionlint.yaml`'s
  runner labels): they're about this repo's infrastructure.
- `renovate-review.yaml`, the `impact/*`, `ori/*` and `owner/approved`
  labels: see Renovate above.
- The release GitHub App: not needed without rulesets.

## Adding an app repo

1. Copy the files above from the closest existing app repo (Go or
   Python), then change the image name, the `area/*` labels, the
   language bits of `build.yaml`, `renovate.json5` and the hooks.
2. Set `PRIVACY_TERMS` (see [Privacy](#privacy)) and run
   `bin/privacy-check`; anything it finds is renamed, not allow-listed.
3. Grant the Renovate app access to the repo.
4. Open it all as one PR, get every check green on that PR, and merge it
   with `gh-merge-safe.sh`. Then `task precommit:init` in every clone.
5. Add the repo to the table at the top of this page.
