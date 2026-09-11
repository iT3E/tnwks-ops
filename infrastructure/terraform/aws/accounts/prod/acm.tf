## ---------------------------------------------------------------------------------------------------------------------
## ACM CERT (us-east-1) for custom Cognito hosted-UI domain
## auth.tnwks.us — Cognito custom domains require the cert to live in us-east-1
## regardless of the user pool's region. Validation goes through Cloudflare DNS;
## the validation CNAMEs are exposed via outputs and added in the cloudflare TF
## workspace in a follow-up PR. Once validated, a separate PR replaces the
## prefix-domain aws_cognito_user_pool_domain with the custom-domain version.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_acm_certificate" "auth_tnwks_us" {
  provider          = aws.us_east_1
  domain_name       = "auth.tnwks.us"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## ACM CERT VALIDATION
## Gates the custom-domain user pool domain on ACM cert issuance. The
## validation CNAME lives in the cloudflare workspace; this resource just
## blocks until ACM polls it and flips the cert to ISSUED.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_acm_certificate_validation" "auth_tnwks_us" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.auth_tnwks_us.arn
  validation_record_fqdns = [for o in aws_acm_certificate.auth_tnwks_us.domain_validation_options : o.resource_record_name]
}
