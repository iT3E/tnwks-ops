## ---------------------------------------------------------------------------------------------------------------------
## LOCALS
## Shared local values.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  cognito_mfa = {
    # tnwks.us (parent zone) so a credential registered through the auth.tnwks.us
    # custom domain works for both internal.tnwks.us subdomains and any future
    # public services on tnwks.us. WebAuthn requires the page origin to equal
    # the RPID or be a subdomain of it.
    relying_party_id  = "tnwks.us"
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
