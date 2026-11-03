# App repos

The repos that hold application source and are deployed by this one follow
one standard: CI on every PR, Renovate, pre-commit hooks, Changie releases,
labels, and secret and privacy scanning. Two public repos keep it:

- [iT3E/app-repo-template](https://github.com/iT3E/app-repo-template), the
  Copier template. Its README is the reference for the standard: what a
  repo gets, how to create one, how updates reach the repos, and how the
  template is released.
- [iT3E/github-workflows](https://github.com/iT3E/github-workflows), what
  the repos run at CI time: the reusable workflows and the Renovate
  presets. Its README documents every workflow, its inputs and secrets,
  and how versions and pins work.

In short, each app repo keeps thin caller workflows pinned to a
github-workflows release (`uses:
iT3E/github-workflows/.github/workflows/<x>.yaml@<sha> # vX.Y.Z`), and
Copier manages the shared files (`.copier-answers.yml`). The template owns
that pin: a github-workflows release becomes a template release, and a
template release reaches every repo as a Renovate `copier update` PR,
labeled `renovate/template`, which that repo's CI tests.

| Repo | Language | Image | Pinned here as |
| --- | --- | --- | --- |
| `iT3E/tnwks-packing-api` | Go | `ghcr.io/it3e/tnwks-packing-api` | `:main` (`kubernetes/apps/production/packing-api`) |
| the storefront API | Go | `ghcr.io/it3e/<repo>` | `:main` (`kubernetes/apps/production/*-things-api`) |
| `iT3E/tnwks-finance-sync` | Python | `ghcr.io/it3e/tnwks-finance-sync` | `:<version>@sha256` (`kubernetes/apps/production/finance-sync`) |
| `iT3E/tnwks-transcriber` | Python | `ghcr.io/it3e/tnwks-transcriber` | `:<version>@sha256` (`kubernetes/apps/production/transcriber`) |

The image tags these pins rely on are part of the standard's contract
(github-workflows' `image.yaml`): `:main` and `:sha-<short>` from `main`
(repos answered `image_push_main: true`), `:<version>` and `:sha-<short>`
from `v*` tags, never `:latest`. Don't change a pin's tag scheme here
without the template.

What stays here:

- The privacy denylist, `.github/privacy-terms.sops.yaml`. Every app repo
  (and the two public repos above) reads it from its `PRIVACY_TERMS`
  Actions secret; re-sync them after `task privacy:edit`
  (`task privacy:sync` in each repo).
- The shared Renovate presets `commitMessage.json5`, `labels.json5` and
  `semanticCommits.json5`, which github-workflows' `renovate/default.json5`
  extends.
- `bin/privacy-check`: app-repo-template's `template/bin/privacy-check`
  (and github-workflows' copy) have the same matching. A change here is
  ported there and released.

Adding an app repo: follow "Create a new app repo" in the
app-repo-template README, then add it to the table above.
