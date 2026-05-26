## ---------------------------------------------------------------------------------------------------------------------
## RESOURCE SERVER
## Defines a custom OAuth2 resource server `tnwks-api` with read/write/admin scopes that the agent client can request.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_resource_server" "tnwks_api" {
  identifier   = "tnwks-api"
  name         = "tnwks-api"
  user_pool_id = aws_cognito_user_pool.this.id

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
## Authorization code flow for the cluster's forward-auth proxy in front of *.internal.tnwks.us services.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "oauth2_proxy" {
  name         = "oauth2-proxy"
  user_pool_id = aws_cognito_user_pool.this.id

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
## Client credentials flow for the Ori agent. Scopes are granted from the tnwks-api resource server.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "agent_ori" {
  name         = "agent-ori"
  user_pool_id = aws_cognito_user_pool.this.id

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
  # Refresh tokens are unused for client_credentials but the field is still required.
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
## DOMAIN
## Hosted UI prefix domain. Use the `auth.tnwks.us` custom domain by setting var.custom_domain + var.acm_cert_arn.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_domain" "prefix" {
  count        = var.custom_domain == null ? 1 : 0
  domain       = var.hosted_ui_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_domain" "custom" {
  count           = var.custom_domain == null ? 0 : 1
  domain          = var.custom_domain
  certificate_arn = var.acm_cert_arn
  user_pool_id    = aws_cognito_user_pool.this.id
}

## ---------------------------------------------------------------------------------------------------------------------
## MANAGED LOGIN BRANDING
## Essentials-tier hosted UI. Empty settings/assets uses the Cognito defaults; customize later via the console
## or by setting the `settings` JSON.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_managed_login_branding" "oauth2_proxy" {
  user_pool_id                = aws_cognito_user_pool.this.id
  client_id                   = aws_cognito_user_pool_client.oauth2_proxy.id
  use_cognito_provided_values = true
}
