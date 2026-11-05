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
  # The homelab-wsl backup credential refresher (docs/operations/backups.md).
  rolesanywhere_backups_common_names = ["homelab-wsl-backups"]
  # homelab-wsl-02 (WSL-02): its own certificate and role. It reads homelab-wsl's
  # backups (CNPG replicas replay its WAL, kopiur restores its snapshots) and
  # writes only its own: CNPG archives under cnpg/<app>/homelab-wsl-02*/
  # (serverName = the site, kubernetes/components/cnpg-cluster) and its own
  # kopiur repository.
  rolesanywhere_backups_sites = {
    "wsl-02" = {
      common_names   = ["homelab-wsl-02-backups"]
      write_prefixes = ["cnpg/*/homelab-wsl-02*/*", "kopiur-homelab-wsl-02/*"]
    }
  }
  sops_role_arn = "arn:aws:iam::654654220436:role/iam-role-sops"
}
