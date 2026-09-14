## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Cognito, SES and the supporting IAM for the prod account.
## ---------------------------------------------------------------------------------------------------------------------

module "aws" {
  source = "../../modules/aws"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  admin_email  = data.sops_file.secrets.data["admin_email"]
  viewer_email = data.sops_file.secrets.data["viewer_email"]
  ses_domain   = data.sops_file.secrets.data["ses_domain"]
}
