## ---------------------------------------------------------------------------------------------------------------------
## SES
## Domain identity, DKIM, and the SMTP IAM user used for outbound mail.
## ---------------------------------------------------------------------------------------------------------------------

## ---------------------------------------------------------------------------------------------------------------------
## SES
## Contains configuration for AWS SES.
##
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_ses_domain_identity" "ses_domain_identity" {
  domain = data.sops_file.secrets.data["ses_domain"]
}

resource "aws_ses_domain_dkim" "ses_domain_dkim" {
  domain = aws_ses_domain_identity.ses_domain_identity.domain
}

resource "aws_iam_user" "smtp_user" {
  name = "smtp_user"
}

resource "aws_iam_access_key" "smtp_user" {
  user = aws_iam_user.smtp_user.name
}

data "aws_iam_policy_document" "ses_sender" {
  statement {
    actions   = ["ses:SendRawEmail"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "ses_sender" {
  name        = "ses_sender"
  description = "Allows sending of e-mails via Simple Email Service"
  policy      = data.aws_iam_policy_document.ses_sender.json
}

resource "aws_iam_user_policy_attachment" "test-attach" {
  user       = aws_iam_user.smtp_user.name
  policy_arn = aws_iam_policy.ses_sender.arn
}
