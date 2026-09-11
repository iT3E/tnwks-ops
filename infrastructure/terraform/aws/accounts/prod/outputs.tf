output "auth_acm_certificate_arn" {
  description = "ARN of the us-east-1 ACM cert for auth.tnwks.us. Consumed by aws_cognito_user_pool_domain in a follow-up apply."
  value       = aws_acm_certificate.auth_tnwks_us.arn
}

output "auth_acm_validation_records" {
  description = "DNS validation CNAMEs to add in the cloudflare workspace so ACM can issue the cert."
  value = [
    for o in aws_acm_certificate.auth_tnwks_us.domain_validation_options : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ]
}

output "cognito_custom_domain_cloudfront_distribution" {
  description = "CloudFront distribution Cognito assigns to the custom domain. Consumed by the cloudflare workspace as the CNAME target for auth.tnwks.us."
  value       = aws_cognito_user_pool_domain.tnwks_auth.cloudfront_distribution
}

## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## ---------------------------------------------------------------------------------------------------------------------

output "cognito_user_pool_id" {
  description = "Cognito user pool ID."
  value       = aws_cognito_user_pool.tnwks_auth.id
}

output "cognito_user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.tnwks_auth.arn
}

output "cognito_user_pool_endpoint" {
  description = "OIDC issuer URL for oauth2-proxy."
  value       = "https://${aws_cognito_user_pool.tnwks_auth.endpoint}"
}

output "cognito_oauth2_proxy_client_id" {
  description = "App client ID for oauth2-proxy."
  value       = aws_cognito_user_pool_client.oauth2_proxy.id
}

output "cognito_oauth2_proxy_client_secret" {
  description = "App client secret for oauth2-proxy."
  value       = aws_cognito_user_pool_client.oauth2_proxy.client_secret
  sensitive   = true
}

output "cognito_onboard_client_id" {
  description = "App client ID for the static onboarding site (onboard.tnwks.us). Public value — copied into kubernetes/apps/auth/onboard/app/static/config.js."
  value       = aws_cognito_user_pool_client.onboard.id
}

output "cognito_agent_client_id" {
  description = "App client ID for the Ori agent."
  value       = aws_cognito_user_pool_client.agent_ori.id
}

output "cognito_agent_client_secret" {
  description = "App client secret for the Ori agent."
  value       = aws_cognito_user_pool_client.agent_ori.client_secret
  sensitive   = true
}

output "cognito_hosted_ui_domain" {
  description = "Fully-qualified hosted UI domain."
  value       = aws_cognito_user_pool_domain.tnwks_auth.domain
}

## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS - SES
## ---------------------------------------------------------------------------------------------------------------------

output "smtp_username" {
  value = aws_iam_access_key.smtp_user.id
}

output "smtp_password" {
  value     = aws_iam_access_key.smtp_user.ses_smtp_password_v4
  sensitive = true
}
