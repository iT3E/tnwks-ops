## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## The iT3E user account. The token is a personal access token from
## secrets.sops.yaml (docs/operations/github.md has the scopes). owner is
## explicit so a GITHUB_OWNER in the environment can't point the provider at
## another account.
## ---------------------------------------------------------------------------------------------------------------------

provider "github" {
  owner = "iT3E"
  token = data.sops_file.secrets.data["github_token"]
}
