# github

Terraform module for a GitHub account's repositories and their settings,
all from one map, `var.repositories`. Called by `environments/prod/github`.
What it manages, what stays by hand, the token and the bootstrap:
[`docs/operations/github.md`](../../../../docs/operations/github.md).

| File              | Manages                                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `repositories.tf` | `github_repository`: one per entry, with secret scanning where set. Never deleted (`prevent_destroy`), archived instead   |
| `rulesets.tf`     | `github_repository_ruleset`: an entry's `rulesets`                                                                        |
| `environments.tf` | `github_repository_environment` and `github_repository_environment_deployment_policy`: an entry's `environments`         |
| `actions.tf`      | `github_actions_repository_permissions`, `github_workflow_repository_permissions`, `github_actions_variable`              |
| `security.tf`     | `github_repository_vulnerability_alerts`: an entry's `vulnerability_alerts`                                               |

Provider: [`integrations/github`](https://registry.terraform.io/providers/integrations/github/latest/docs).
The root pins the version and configures the owner and token.

## Notes

- **Defaults.** Each setting defaults to what GitHub gives a new
  repository, so an entry lists only what differs. The optional parts
  (`vulnerability_alerts`, `actions`, `workflow_permissions`, `variables`,
  `environments`, `rulesets`) are managed only where an entry sets them.
- **Never a delete.** `prevent_destroy` on every repository, and
  `archive_on_destroy` to archive instead should the guard be lifted. No
  `template`, `fork` or `source_*` arguments: on an existing repository they
  force a replacement. GitHub never reports `archive_on_destroy`, so it
  lives only in the state: a repository imported later plans as an import
  plus an in-place update for it. The deprecated `private`,
  `default_branch`, `vulnerability_alerts`, `pages` and `has_downloads`
  arguments stay unset.
- **Private repositories** get no `security_and_analysis` or `rulesets` on
  the account's current plan; a validation rejects them.
- **Secret scanning.** The provider reads back only secret scanning and push
  protection, so those are the two `security_and_analysis` settings.
- **Rulesets.** The provider can't set the pull request rule's
  `require_extra_approval_for_unattributed_changes`; an update of the
  ruleset may reset it (`rulesets.tf`).
- **Names.** An entry's key is the repository name unless it sets `name`.
  The root uses that for private repositories whose names stay out of the
  code: a neutral key (`private1`), the name from SOPS. Keys are state
  addresses and show in CI output, so they never carry a name, and they're
  never renamed. No output carries a name.
- **Keys** are `<repo>/<name>` for rulesets, environments and variables, and
  `<repo>/<environment>/<pattern>` for deployment policies.
