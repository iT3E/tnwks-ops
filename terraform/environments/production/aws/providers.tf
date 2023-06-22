provider "aws" {
  access_key                  = data.sops_file.secrets.data["aws_access_key"]
  secret_key                  = data.sops_file.secrets.data["aws_secret_key"]
  region                      = data.sops_file.secrets.data["aws_region"]
}
