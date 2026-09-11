## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## All Terraform providers.
##
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  region     = data.sops_file.secrets.data["aws_region"]
  access_key = data.sops_file.secrets.data["aws_access_key"]
  secret_key = data.sops_file.secrets.data["aws_secret_key"]
}
