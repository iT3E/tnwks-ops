## ---------------------------------------------------------------------------------------------------------------------
## IAM USERS
## The SMTP user SES hands to applications, its access key and policy
## attachment.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_iam_user" "smtp_user" {
  name = "smtp_user"
}

resource "aws_iam_access_key" "smtp_user" {
  user = aws_iam_user.smtp_user.name
}

resource "aws_iam_user_policy_attachment" "test-attach" {
  user       = aws_iam_user.smtp_user.name
  policy_arn = aws_iam_policy.ses_sender.arn
}

# The homelab-wsl cluster's backup writer (barman-cloud and kopiur). Its key
# goes into the cluster's SOPS secrets; see docs/operations/backups.md.
resource "aws_iam_user" "homelab_backups" {
  name = "homelab_backups"
}

resource "aws_iam_access_key" "homelab_backups" {
  user = aws_iam_user.homelab_backups.name
}

resource "aws_iam_user_policy_attachment" "homelab_backups" {
  user       = aws_iam_user.homelab_backups.name
  policy_arn = aws_iam_policy.homelab_backups.arn
}
