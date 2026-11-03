# App repos

The repos that hold application source and are deployed by this one follow
one standard, kept in [iT3E/app-template](https://github.com/iT3E/app-template):
CI on every PR, Renovate, pre-commit hooks, Changie releases, labels, and
secret and privacy scanning. Its README is the reference: what a repo gets,
the reusable workflows and their inputs, how to create a repo, how
template updates reach the repos, and how the template is released.

In short, each app repo keeps thin caller workflows pinned to a template
release (`uses: iT3E/app-template/.github/workflows/<x>.yaml@<sha> #
vX.Y.Z`), and Copier manages the shared files (`.copier-answers.yml`). A
template release reaches every repo as a Renovate `copier update` PR,
labeled `renovate/template`, which that repo's CI tests.

| Repo | Language | Image | Pinned here as |
| --- | --- | --- | --- |
| `iT3E/tnwks-packing-api` | Go | `ghcr.io/it3e/tnwks-packing-api` | `:main` (`kubernetes/apps/production/packing-api`) |
| the storefront API | Go | `ghcr.io/it3e/<repo>` | `:main` (`kubernetes/apps/production/*-things-api`) |
| `iT3E/tnwks-finance-sync` | Python | `ghcr.io/it3e/tnwks-finance-sync` | `:<version>@sha256` (`kubernetes/apps/production/finance-sync`) |

The image tags these pins rely on are part of the template's contract:
`:main` and `:sha-<short>` from `main` (repos answered
`image_push_main: true`), `:<version>` and `:sha-<short>` from `v*` tags,
never `:latest`. Don't change a pin's tag scheme here without the template.

What stays here:

- The privacy denylist, `.github/privacy-terms.sops.yaml`. Every app repo
  (and app-template, which is public) reads it from its `PRIVACY_TERMS`
  Actions secret; re-sync them after `task privacy:edit`
  (`task privacy:sync` in each repo).
- The shared Renovate presets `commitMessage.json5`, `labels.json5` and
  `semanticCommits.json5`, which app-template's `renovate/default.json5`
  extends.
- `bin/privacy-check`: app-template's copy has the same matching. A change
  here is ported to `template/bin/privacy-check` there and released.

Adding an app repo: follow "Create a new app repo" in the template README,
then add it to the table above.
