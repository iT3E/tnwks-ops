## ---------------------------------------------------------------------------------------------------------------------
## SES
## Domain identity + DKIM signing for outbound mail.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_ses_domain_identity" "ses_domain_identity" {
  domain = var.ses_domain
}

resource "aws_ses_domain_dkim" "ses_domain_dkim" {
  domain = aws_ses_domain_identity.ses_domain_identity.domain
}
