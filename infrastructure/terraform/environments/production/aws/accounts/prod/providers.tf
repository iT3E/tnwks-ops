provider "aws" {
  profile                     = "sso"
  region                      = data.sops_file.secrets.data["aws_region"]
}
