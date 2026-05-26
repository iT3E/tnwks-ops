## ---------------------------------------------------------------------------------------------------------------------
## PROVIDER
## Assumes the cross-account role in the prod AWS account where Cognito lives.
## ---------------------------------------------------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region
  assume_role {
    role_arn = "arn:aws:iam::654654262098:role/tnwks-org-init-role"
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## TERRAFORM
## Terraform Cloud workspace + required providers. user_pool_tier, web_authn_configuration, sign_in_policy,
## and aws_cognito_managed_login_branding require AWS provider >= 5.85.
## ---------------------------------------------------------------------------------------------------------------------

terraform {
  cloud {
    hostname     = "app.terraform.io"
    organization = "tnwks-ops"
    workspaces {
      name = "tnwks-ops-aws-cognito"
    }
  }
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## DATA / LOCALS
## ---------------------------------------------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  pool_name = "tnwks-auth"
  tags = {
    Environment = "prod"
    Component   = "auth"
    ManagedBy   = "terraform"
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## USER POOL
## Essentials tier: passkeys (WebAuthn), TOTP MFA, managed login branding, choice-based sign-in.
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_pool" "this" {
  name           = local.pool_name
  user_pool_tier = "ESSENTIALS"

  # Email serves as both username and a verified attribute.
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

  # MFA required. Both TOTP authenticator apps and passkeys (WebAuthn) are accepted.
  mfa_configuration = "ON"

  software_token_mfa_configuration {
    enabled = true
  }

  web_authn_configuration {
    relying_party_id  = var.relying_party_id
    user_verification = "preferred"
  }

  # Allow passkey-only first-factor login in addition to password.
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

  tags = local.tags
}

## ---------------------------------------------------------------------------------------------------------------------
## GROUPS
## admins  - full access
## viewers - read-only
## agents  - machine-to-machine
## ---------------------------------------------------------------------------------------------------------------------

resource "aws_cognito_user_group" "admins" {
  name         = "admins"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Full access to internal services"
  precedence   = 1
}

resource "aws_cognito_user_group" "viewers" {
  name         = "viewers"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Read-only access to internal services"
  precedence   = 10
}

resource "aws_cognito_user_group" "agents" {
  name         = "agents"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Machine-to-machine clients"
  precedence   = 20
}
