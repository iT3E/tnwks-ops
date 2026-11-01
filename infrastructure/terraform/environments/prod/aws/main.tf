## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Cognito, SES and the supporting IAM for the prod account.
## ---------------------------------------------------------------------------------------------------------------------

module "aws" {
  source = "../../../modules/aws"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  admin_email  = data.sops_file.secrets.data["admin_email"]
  viewer_email = data.sops_file.secrets.data["viewer_email"]
  kiosk_email  = data.sops_file.secrets.data["kiosk_email"]
  ses_domain   = data.sops_file.secrets.data["ses_domain"]

  # IAM Roles Anywhere for SOPS: CA cert and issuing script in ./rolesanywhere.
  # The CNs are the workstations allowed a session; see rolesanywhere/README.md.
  rolesanywhere_ca_pem            = file("${path.module}/rolesanywhere/ca.pem")
  rolesanywhere_sops_common_names = ["it3e-wsl"]
  sops_role_arn                   = "arn:aws:iam::654654220436:role/iam-role-sops"
}
