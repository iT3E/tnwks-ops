## ---------------------------------------------------------------------------------------------------------------------
## MAIN
## Cognito, SES and the supporting IAM for the prod account, and the AWS parts
## of the security platform across both accounts, plus consolidated billing.
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
  # The Kopia repository of the agent trust domains on homelab-wsl, with its own
  # certificate: the session that the backup operator copies into an agent
  # namespace reaches only this prefix (docs/apps/agent-platform.md, "Backups of
  # the agent trust domains").
  rolesanywhere_backups_prefixed = {
    "agents" = {
      common_names = ["homelab-wsl-agent-backups"]
      prefix       = "kopiur-agents/"
      # The bucket policy denies kopiur-agents/ to every other principal but
      # the break-glass role below (modules/aws/s3_backups.tf).
      exclusive = true
    }
    # The repository of the finance trust domain only: one repository for each
    # trust domain, so a mover in agents-finance reaches no other snapshot
    # (docs/apps/agent-platform.md, "Backups of the agent trust domains").
    # Not kopiur-agents-finance/: the deny of a list for "agents" matches every
    # prefix that starts with kopiur-agents, and would refuse the list of this
    # role. No exclusive prefix may start with the name of another one.
    "agents-finance" = {
      common_names = ["homelab-wsl-agents-finance-backups"]
      prefix       = "kopiur-finance/"
      exclusive    = true
    }
    # The repository of the main trust domain only, for the same reason. Not
    # kopiur-agents-main/, as above.
    "agents-main" = {
      common_names = ["homelab-wsl-agents-main-backups"]
      prefix       = "kopiur-main/"
      exclusive    = true
    }
  }
  # The owner's way into an exclusive prefix: the AdministratorAccess
  # permission set of IAM Identity Center in this account.
  backups_break_glass_role_arn_patterns = [
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_AdministratorAccess_*",
  ]
  sops_role_arn = "arn:aws:iam::654654220436:role/iam-role-sops"
}

# The AWS parts of the security platform (modules/aws/security): the
# organization trail and the audit role of the management account, the trail
# bucket in the prod account, and the two read-only Roles Anywhere roles that
# homelab-wsl-02 gets its sessions from. Each role trusts one certificate CN.
module "security" {
  source = "../../../modules/aws/security"

  providers = {
    aws            = aws
    aws.management = aws.management
  }

  rolesanywhere_trust_anchor_arn   = module.aws.rolesanywhere_trust_anchor_arn
  rolesanywhere_audit_common_names = ["homelab-wsl-02-security-audit"]
  rolesanywhere_trail_common_names = ["homelab-wsl-02-security-trail"]
}

module "billing" {
  source = "../../../modules/aws/billing"

  providers = {
    aws = aws.management
  }

  monthly_budget_usd = 50
  subscriber_email   = data.sops_file.secrets.data["admin_email"]
  prod_account_id    = data.aws_caller_identity.current.account_id
}
