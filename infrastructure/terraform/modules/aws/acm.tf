## ---------------------------------------------------------------------------------------------------------------------
## ACM
## Certificate for the Cognito custom auth domain. Must live in us-east-1
## regardless of the user pool's region, so it uses the aliased provider.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_acm_certificate" "auth_tnwks_us" {
  provider          = aws.us_east_1
  domain_name       = "auth.tnwks.us"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "auth_tnwks_us" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.auth_tnwks_us.arn
  validation_record_fqdns = [for o in aws_acm_certificate.auth_tnwks_us.domain_validation_options : o.resource_record_name]
}
