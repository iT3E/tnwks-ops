## ---------------------------------------------------------------------------------------------------------------------
## OUTPUTS
## Re-exported from the module so workspace output names are unchanged.
## ---------------------------------------------------------------------------------------------------------------------

output "auth_acm_certificate_arn" {
  description = "ARN of the us-east-1 ACM cert for auth.tnwks.us. Consumed by aws_cognito_user_pool_domain in a follow-up apply."
  value       = module.aws.auth_acm_certificate_arn
}

output "auth_acm_validation_records" {
  description = "DNS validation CNAMEs to add in the cloudflare workspace so ACM can issue the cert."
  value       = module.aws.auth_acm_validation_records
}

output "cognito_agent_client_id" {
  description = "App client ID for the Ori agent."
  value       = module.aws.cognito_agent_client_id
}

output "cognito_agent_client_secret" {
  description = "App client secret for the Ori agent."
  value       = module.aws.cognito_agent_client_secret
  sensitive   = true
}

output "cognito_custom_domain_cloudfront_distribution" {
  description = "CloudFront distribution Cognito assigns to the custom domain. Consumed by the cloudflare workspace as the CNAME target for auth.tnwks.us."
  value       = module.aws.cognito_custom_domain_cloudfront_distribution
}

output "cognito_hosted_ui_domain" {
  description = "Fully-qualified hosted UI domain."
  value       = module.aws.cognito_hosted_ui_domain
}

output "cognito_immich_client_id" {
  description = "App client ID for Immich (built-in OAuth)."
  value       = module.aws.cognito_immich_client_id
}

output "cognito_immich_client_secret" {
  description = "App client secret for Immich. Copied into the immich Secret (SOPS) in kubernetes/apps/selfhosted/immich/app."
  value       = module.aws.cognito_immich_client_secret
  sensitive   = true
}

output "cognito_mealie_client_id" {
  description = "App client ID for Mealie (built-in OIDC)."
  value       = module.aws.cognito_mealie_client_id
}

output "cognito_mealie_client_secret" {
  description = "App client secret for Mealie. Copied into the mealie Secret (SOPS) in kubernetes/apps/production/mealie/app."
  value       = module.aws.cognito_mealie_client_secret
  sensitive   = true
}

output "cognito_oauth2_proxy_client_id" {
  description = "App client ID for oauth2-proxy."
  value       = module.aws.cognito_oauth2_proxy_client_id
}

output "cognito_oauth2_proxy_client_secret" {
  description = "App client secret for oauth2-proxy."
  value       = module.aws.cognito_oauth2_proxy_client_secret
  sensitive   = true
}

output "cognito_home_assistant_client_id" {
  description = "App client ID for Home Assistant (hass-oidc-auth). Public value — copied into kubernetes/apps/home-automation/home-assistant/app/helmrelease.yaml."
  value       = module.aws.cognito_home_assistant_client_id
}

output "cognito_onboard_client_id" {
  description = "App client ID for the static onboarding site (onboard.tnwks.us). Public value — copied into kubernetes/apps/auth/onboard/app/static/config.js."
  value       = module.aws.cognito_onboard_client_id
}

output "cognito_user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = module.aws.cognito_user_pool_arn
}

output "cognito_user_pool_endpoint" {
  description = "OIDC issuer URL for oauth2-proxy."
  value       = module.aws.cognito_user_pool_endpoint
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID."
  value       = module.aws.cognito_user_pool_id
}

output "smtp_username" {
  value = module.aws.smtp_username
}

output "smtp_password" {
  value     = module.aws.smtp_password
  sensitive = true
}

output "rolesanywhere_trust_anchor_arn" {
  description = "Roles Anywhere trust anchor ARN."
  value       = module.aws.rolesanywhere_trust_anchor_arn
}

output "rolesanywhere_sops_profile_arn" {
  description = "Roles Anywhere profile ARN for SOPS sessions."
  value       = module.aws.rolesanywhere_sops_profile_arn
}

output "rolesanywhere_sops_role_arn" {
  description = "Role that Roles Anywhere SOPS sessions assume."
  value       = module.aws.rolesanywhere_sops_role_arn
}
