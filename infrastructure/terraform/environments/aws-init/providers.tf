## ---------------------------------------------------------------------------------------------------------------------
## PROVIDERS
## This root bootstraps the org, so it predates the OIDC role it creates
## and authenticates with static keys from SOPS.
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  region     = data.sops_file.secrets.data["aws_region"]
  access_key = data.sops_file.secrets.data["aws_access_key"]
  secret_key = data.sops_file.secrets.data["aws_secret_key"]
}
