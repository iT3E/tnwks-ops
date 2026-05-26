output "user_pool_id" {
  description = "Cognito user pool ID."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.this.arn
}

output "user_pool_endpoint" {
  description = "Cognito user pool issuer endpoint (use as oauth2-proxy --oidc-issuer-url)."
  value       = "https://${aws_cognito_user_pool.this.endpoint}"
}

output "oauth2_proxy_client_id" {
  description = "App client ID for oauth2-proxy."
  value       = aws_cognito_user_pool_client.oauth2_proxy.id
}

output "oauth2_proxy_client_secret" {
  description = "App client secret for oauth2-proxy."
  value       = aws_cognito_user_pool_client.oauth2_proxy.client_secret
  sensitive   = true
}

output "agent_client_id" {
  description = "App client ID for the Ori agent."
  value       = aws_cognito_user_pool_client.agent_ori.id
}

output "agent_client_secret" {
  description = "App client secret for the Ori agent."
  value       = aws_cognito_user_pool_client.agent_ori.client_secret
  sensitive   = true
}

output "hosted_ui_domain" {
  description = "Fully-qualified hosted UI domain."
  value = (
    var.custom_domain == null
    ? "${aws_cognito_user_pool_domain.prefix[0].domain}.auth.${var.aws_region}.amazoncognito.com"
    : aws_cognito_user_pool_domain.custom[0].domain
  )
}

output "resource_server_identifier" {
  description = "Identifier of the tnwks-api resource server (used as the audience prefix for custom scopes)."
  value       = aws_cognito_resource_server.tnwks_api.identifier
}
