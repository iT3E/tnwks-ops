## ---------------------------------------------------------------------------------------------------------------------
## IAM POLICIES
## Policy documents and managed policies.
## ---------------------------------------------------------------------------------------------------------------------

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
