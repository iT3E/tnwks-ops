## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## healthchecks.io API. The key is the project's read-write API key
## (Project Settings > API Access), from secrets.sops.yaml.
## ---------------------------------------------------------------------------------------------------------------------

provider "healthchecksio" {
  api_key = data.sops_file.secrets.data["api_key"]
}
