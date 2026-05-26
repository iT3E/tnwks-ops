## ---------------------------------------------------------------------------------------------------------------------
## COGNITO USER POOL
## tnwks-auth: Essentials tier user pool backing oauth2-proxy in front of *.internal.tnwks.us
## and issuing M2M tokens to the Ori agent. Passkeys + TOTP MFA, admin-create only.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool" "tnwks_auth" {
  name           = "tnwks-auth"
  user_pool_tier = "ESSENTIALS"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # MFA + WebAuthn are configured by the terraform_data.cognito_mfa resource
  # via SetUserPoolMfaConfig — the provider's web_authn_configuration block
  # is missing the FactorConfiguration field
  # (hashicorp/terraform-provider-aws#47598), so a native MFA apply is rejected
  # by AWS. sign_in_policy stays here because the provider handles it safely.
  mfa_configuration = "OFF"

  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD", "WEB_AUTHN"]
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  schema {
    name                     = "email"
    attribute_data_type      = "String"
    mutable                  = true
    required                 = true
    developer_only_attribute = false

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  deletion_protection = "ACTIVE"

  lifecycle {
    ignore_changes = [
      mfa_configuration,
      software_token_mfa_configuration,
      web_authn_configuration,
    ]
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## MFA CONFIGURATION
## Workaround for hashicorp/terraform-provider-aws#47598 (provider's
## web_authn_configuration block is missing the FactorConfiguration field).
## Calls SetUserPoolMfaConfig via the AWS CLI on every change; that API only
## mutates MFA-related fields, so it's safe to use without a read-modify-write.
## On destroy, reverts MFA to OFF so deletion isn't blocked.
##
## TFC dynamic credentials inject AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN into
## the run env (TFC_AWS_PROVIDER_AUTH=true), which local-exec inherits.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  cognito_mfa = {
    relying_party_id     = "internal.tnwks.us"
    user_verification    = "required"
    factor_configuration = "MULTI_FACTOR_WITH_USER_VERIFICATION"
  }
}

resource "terraform_data" "cognito_mfa" {
  triggers_replace = [
    aws_cognito_user_pool.tnwks_auth.id,
    local.cognito_mfa,
    data.aws_region.current.name,
  ]

  provisioner "local-exec" {
    when    = create
    command = <<-EOT
      set -eu
      aws cognito-idp set-user-pool-mfa-config \
        --region ${data.aws_region.current.name} \
        --user-pool-id ${aws_cognito_user_pool.tnwks_auth.id} \
        --mfa-configuration ON \
        --software-token-mfa-configuration Enabled=true \
        --web-authn-configuration RelyingPartyId=${local.cognito_mfa.relying_party_id},UserVerification=${local.cognito_mfa.user_verification},FactorConfiguration=${local.cognito_mfa.factor_configuration}
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws cognito-idp set-user-pool-mfa-config --region ${self.triggers_replace[2]} --user-pool-id ${self.triggers_replace[0]} --mfa-configuration OFF"
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## GROUPS
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_group" "admins" {
  name         = "admins"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id
  description  = "Full access to internal services"
  precedence   = 1
}

resource "aws_cognito_user_group" "viewers" {
  name         = "viewers"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id
  description  = "Read-only access to internal services"
  precedence   = 10
}

resource "aws_cognito_user_group" "agents" {
  name         = "agents"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id
  description  = "Machine-to-machine clients"
  precedence   = 20
}

## ---------------------------------------------------------------------------------------------------------------------
## RESOURCE SERVER
## Custom OAuth2 resource server with read/write/admin scopes for the agent client.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_resource_server" "tnwks_api" {
  identifier   = "tnwks-api"
  name         = "tnwks-api"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id

  scope {
    scope_name        = "read"
    scope_description = "Read access to tnwks internal APIs"
  }

  scope {
    scope_name        = "write"
    scope_description = "Write access to tnwks internal APIs"
  }

  scope {
    scope_name        = "admin"
    scope_description = "Admin access to tnwks internal APIs"
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## APP CLIENT - oauth2-proxy
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "oauth2_proxy" {
  name         = "oauth2-proxy"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id

  generate_secret = true

  callback_urls = ["https://oauth2.internal.tnwks.us/oauth2/callback"]
  logout_urls   = ["https://oauth2.internal.tnwks.us/oauth2/sign_out"]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  access_token_validity  = 24
  id_token_validity      = 24
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

## ---------------------------------------------------------------------------------------------------------------------
## APP CLIENT - agent-ori
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "agent_ori" {
  name         = "agent-ori"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id

  generate_secret = true

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes = [
    "${aws_cognito_resource_server.tnwks_api.identifier}/read",
    "${aws_cognito_resource_server.tnwks_api.identifier}/write",
  ]

  supported_identity_providers = ["COGNITO"]

  access_token_validity = 24
  id_token_validity     = 24
  # Required field even though client_credentials doesn't issue refresh tokens.
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

## ---------------------------------------------------------------------------------------------------------------------
## HOSTED UI DOMAIN (prefix)
## tnwks-auth.auth.us-east-1.amazoncognito.com
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_domain" "tnwks_auth" {
  domain       = "tnwks-auth"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id
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
  value       = "${aws_cognito_user_pool_domain.tnwks_auth.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}
