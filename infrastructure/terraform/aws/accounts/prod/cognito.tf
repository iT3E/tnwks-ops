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

  # Cognito requires PASSWORD to remain in allowed_first_auth_factors —
  # AWS rejects pool updates that remove it ("Password should be configured
  # as one of the allowed first auth factors"). This is a Cognito limitation,
  # not a TF provider gap.
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
    relying_party_id  = "internal.tnwks.us"
    user_verification = "required"
    # MULTI_FACTOR_WITH_USER_VERIFICATION: passkey acts as MFA (second factor
    # after password). The other valid value is SINGLE_FACTOR (passwordless),
    # but AWS rejects that whenever PASSWORD is also a first-auth factor +
    # MFA is ON, which it must be for password-path TOTP to be enforced.
    # See [[cognito-passwordless-and-mfa-incompatible]].
    factor_configuration = "MULTI_FACTOR_WITH_USER_VERIFICATION"
  }

  # Same role the AWS provider assumes in main.tf — TFC dynamic credentials
  # land in the org management account, so the local-exec has to re-assume
  # this role to reach the prod account where the user pool lives.
  cognito_mfa_role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
}

resource "terraform_data" "cognito_mfa" {
  triggers_replace = [
    aws_cognito_user_pool.tnwks_auth.id,
    local.cognito_mfa,
    data.aws_region.current.name,
    local.cognito_mfa_role_arn,
  ]

  provisioner "local-exec" {
    when    = create
    command = <<-EOT
      set -eu
      creds=$(aws sts assume-role \
        --role-arn ${local.cognito_mfa_role_arn} \
        --role-session-name tfc-cognito-mfa \
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
        --output text)
      export AWS_ACCESS_KEY_ID=$(echo "$creds" | cut -f1)
      export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | cut -f2)
      export AWS_SESSION_TOKEN=$(echo "$creds" | cut -f3)
      aws cognito-idp set-user-pool-mfa-config \
        --region ${data.aws_region.current.name} \
        --user-pool-id ${aws_cognito_user_pool.tnwks_auth.id} \
        --mfa-configuration ON \
        --software-token-mfa-configuration Enabled=true \
        --web-authn-configuration RelyingPartyId=${local.cognito_mfa.relying_party_id},UserVerification=${local.cognito_mfa.user_verification},FactorConfiguration=${local.cognito_mfa.factor_configuration}
    EOT
  }

  # No destroy provisioner: replacement of this resource (e.g. when changing
  # local.cognito_mfa) was setting MFA=OFF *before* the create-side ran, which
  # in turn made aws_cognito_user_pool.tnwks_auth's UpdateUserPool fail with
  # "Cannot turn MFA functionality ON, once the user pool has been created"
  # because the provider tried to push the (TF-state) mfa_configuration back
  # in the same plan. The create provisioner is authoritative for the pool's
  # MFA config either way; on real destroy the pool itself is deletion-
  # protected, so a parting MFA=OFF call serves no purpose.
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
## USERS
## Human admin(s). Email is sourced from secrets.sops.yaml so the value
## doesn't land in the public repo.
##
## Pool's sign_in_policy enables PASSWORD as a first-auth factor, so
## AdminCreateUser requires a TemporaryPassword (AWS won't generate one
## itself). We hand in a random_password — the user changes it on first
## login. The temp password is in TF state only; not output.
## ---------------------------------------------------------------------------------------------------------------------

resource "random_password" "admin_temp" {
  length           = 24
  special          = true
  override_special = "!@#$%^&*()-_=+"

  # Rotate the temp password whenever the username changes (e.g. a new
  # admin) or when the pool's auth posture changes (forces a fresh user +
  # invitation email so enrollment starts clean).
  keepers = {
    username     = data.sops_file.secrets.data["admin_email"]
    auth_posture = "passkey-as-mfa-with-totp"
  }
}

resource "aws_cognito_user" "admin" {
  user_pool_id       = aws_cognito_user_pool.tnwks_auth.id
  username           = data.sops_file.secrets.data["admin_email"]
  temporary_password = random_password.admin_temp.result

  attributes = {
    email          = data.sops_file.secrets.data["admin_email"]
    email_verified = true
  }

  desired_delivery_mediums = ["EMAIL"]

  # On re-applies the user has already rotated this password; don't
  # try to reset it back to the temp value.
  # replace_triggered_by ties user replacement to random_password.admin_temp,
  # so changing the random_password.keepers (auth_posture, username) forces a
  # fresh user + invitation email — which is what we want when flipping the
  # pool's auth posture (e.g. password+TOTP -> WebAuthn-only).
  lifecycle {
    ignore_changes = [temporary_password]
    replace_triggered_by = [
      random_password.admin_temp,
    ]
  }
}

resource "aws_cognito_user_in_group" "admin_in_admins" {
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id
  group_name   = aws_cognito_user_group.admins.name
  username     = aws_cognito_user.admin.username
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

  # Second callback_url is the post-enrollment landing for the
  # /passkeys/add managed-login flow.
  callback_urls = [
    "https://oauth2.internal.tnwks.us/oauth2/callback",
    "https://grafana.internal.tnwks.us/",
  ]
  logout_urls = ["https://oauth2.internal.tnwks.us/oauth2/sign_out"]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]

  # ALLOW_USER_AUTH enables choice-based auth, which is the only flow that
  # supports passkeys per AWS docs. ALLOW_USER_SRP_AUTH stays as the OIDC
  # path oauth2-proxy uses today.
  explicit_auth_flows = [
    "ALLOW_USER_AUTH",
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
## MANAGED LOGIN BRANDING - oauth2-proxy
## Opts the oauth2-proxy app client into the Managed Login experience (vs the
## classic Hosted UI). Managed Login prompts users to enroll a passkey after a
## successful password sign-in, so subsequent logins on new devices complete
## with WebAuthn alone.
##
## aws_cognito_managed_login_branding is only available in provider 6.12.0+
## (we're on ~> 5.0), so we drive it via the AWS CLI on every change — same
## pattern as terraform_data.cognito_mfa above. UseCognitoProvidedValues=true
## keeps the default look; no assets or settings to manage in TF.
## ---------------------------------------------------------------------------------------------------------------------

resource "terraform_data" "oauth2_proxy_managed_login" {
  triggers_replace = [
    aws_cognito_user_pool.tnwks_auth.id,
    aws_cognito_user_pool_client.oauth2_proxy.id,
    data.aws_region.current.name,
    local.cognito_mfa_role_arn,
  ]

  provisioner "local-exec" {
    when    = create
    command = <<-EOT
      set -eu
      creds=$(aws sts assume-role \
        --role-arn ${local.cognito_mfa_role_arn} \
        --role-session-name tfc-managed-login \
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
        --output text)
      export AWS_ACCESS_KEY_ID=$(echo "$creds" | cut -f1)
      export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | cut -f2)
      export AWS_SESSION_TOKEN=$(echo "$creds" | cut -f3)
      # CreateManagedLoginBranding errors with ManagedLoginBrandingExistsException
      # if a branding already exists for the client; check first so re-applies
      # are idempotent.
      existing=$(aws cognito-idp describe-managed-login-branding-by-client \
        --region ${data.aws_region.current.name} \
        --user-pool-id ${aws_cognito_user_pool.tnwks_auth.id} \
        --client-id ${aws_cognito_user_pool_client.oauth2_proxy.id} \
        --query 'ManagedLoginBranding.ManagedLoginBrandingId' \
        --output text 2>/dev/null) || existing=""
      if [ -z "$existing" ] || [ "$existing" = "None" ]; then
        aws cognito-idp create-managed-login-branding \
          --region ${data.aws_region.current.name} \
          --user-pool-id ${aws_cognito_user_pool.tnwks_auth.id} \
          --client-id ${aws_cognito_user_pool_client.oauth2_proxy.id} \
          --use-cognito-provided-values
      fi
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      creds=$(aws sts assume-role \
        --role-arn ${self.triggers_replace[3]} \
        --role-session-name tfc-managed-login \
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
        --output text)
      export AWS_ACCESS_KEY_ID=$(echo "$creds" | cut -f1)
      export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | cut -f2)
      export AWS_SESSION_TOKEN=$(echo "$creds" | cut -f3)
      branding_id=$(aws cognito-idp describe-managed-login-branding-by-client \
        --region ${self.triggers_replace[2]} \
        --user-pool-id ${self.triggers_replace[0]} \
        --client-id ${self.triggers_replace[1]} \
        --query 'ManagedLoginBranding.ManagedLoginBrandingId' \
        --output text 2>/dev/null) || branding_id=""
      if [ -n "$branding_id" ] && [ "$branding_id" != "None" ]; then
        aws cognito-idp delete-managed-login-branding \
          --region ${self.triggers_replace[2]} \
          --user-pool-id ${self.triggers_replace[0]} \
          --managed-login-branding-id "$branding_id"
      fi
    EOT
  }
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

## ---------------------------------------------------------------------------------------------------------------------
## HOSTED UI DOMAIN (prefix)
## tnwks-auth.auth.us-east-1.amazoncognito.com
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool_domain" "tnwks_auth" {
  domain       = "tnwks-auth"
  user_pool_id = aws_cognito_user_pool.tnwks_auth.id

  # version 2 = Managed Login (newer hosted UI, supports passkey enrollment
  # at /passkeys/add). version 1 = classic Hosted UI which doesn't have
  # the passkey endpoints — /passkeys/add returns "URL doesn't exist on the
  # authorization server" without this.
  managed_login_version = 2
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
