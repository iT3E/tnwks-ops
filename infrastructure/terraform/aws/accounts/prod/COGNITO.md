# Cognito — `tnwks-auth`

The `cognito.tf` resources in this directory provision the AWS Cognito user
pool that backs authentication for `*.internal.tnwks.us` services
(via oauth2-proxy) and issues machine-to-machine tokens to the Ori agent.

## What it creates

- **User pool** `tnwks-auth` (Essentials tier — passkeys, managed login,
  10k free MAU)
- **Groups:** `admins`, `viewers`, `agents`
- **Resource server** `tnwks-api` with scopes `read` / `write` / `admin`
- **App clients:**
  - `oauth2-proxy` — authorization_code, callback to
    `https://oauth2.internal.tnwks.us/oauth2/callback`, scopes
    `openid email profile`, with secret
  - `agent-ori` — client_credentials, scopes `tnwks-api/read`,
    `tnwks-api/write`, with secret
- **Hosted UI domain:** prefix `tnwks-auth.auth.us-west-2.amazoncognito.com`
- **MFA:** required, TOTP + WebAuthn passkeys; passwordless first-factor
  login allowed *(configured out-of-band — see [Enable MFA](#enable-mfa-one-time)
  below)*
- **Password policy:** 12+ chars, mixed case, numbers, symbols
- **Token lifetimes:** access 24h, id 24h, refresh 30d
- **Sign-up:** disabled (admin-create only)
- **Deletion protection:** on

## Outputs

| Output                                | Notes      |
| ------------------------------------- | ---------- |
| `cognito_user_pool_id`                |            |
| `cognito_user_pool_arn`               |            |
| `cognito_user_pool_endpoint`          | OIDC issuer URL |
| `cognito_oauth2_proxy_client_id`      |            |
| `cognito_oauth2_proxy_client_secret`  | sensitive  |
| `cognito_agent_client_id`             |            |
| `cognito_agent_client_secret`         | sensitive  |
| `cognito_hosted_ui_domain`            |            |

## Enable MFA (one-time)

Terraform creates the pool with `mfa_configuration = "OFF"` because the AWS
provider's `web_authn_configuration` block is missing the `FactorConfiguration`
field that AWS requires when WebAuthn is an allowed first-auth-factor — tracked
in [hashicorp/terraform-provider-aws#47598](https://github.com/hashicorp/terraform-provider-aws/issues/47598).
Run this once after the first apply:

```bash
POOL_ID="$(terraform output -raw cognito_user_pool_id)"

aws cognito-idp set-user-pool-mfa-config \
  --user-pool-id "$POOL_ID" \
  --mfa-configuration ON \
  --software-token-mfa-configuration Enabled=true \
  --web-authn-configuration RelyingPartyId=internal.tnwks.us,UserVerification=required

aws cognito-idp update-user-pool \
  --user-pool-id "$POOL_ID" \
  --policies '{
    "SignInPolicy": {
      "AllowedFirstAuthFactors": ["PASSWORD", "WEB_AUTHN"]
    }
  }'
```

The pool resource has `lifecycle.ignore_changes` on `mfa_configuration`,
`software_token_mfa_configuration`, `web_authn_configuration`, and
`sign_in_policy` so subsequent Terraform plans won't try to revert this.

When the upstream provider gains `factor_configuration` support, fold these
back into `cognito.tf` and drop the lifecycle ignores.

## Adding a user

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$(terraform output -raw cognito_user_pool_id)" \
  --username alice@example.com \
  --user-attributes Name=email,Value=alice@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
```

## Adding a user to a group

```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$(terraform output -raw cognito_user_pool_id)" \
  --username alice@example.com \
  --group-name admins   # or viewers, agents
```

## Adding a new group

Append a new `aws_cognito_user_group` resource to `cognito.tf`.

## Wiring oauth2-proxy

```
--provider=oidc
--oidc-issuer-url=<cognito_user_pool_endpoint output>
--client-id=<cognito_oauth2_proxy_client_id output>
--client-secret=<cognito_oauth2_proxy_client_secret output>
--scope="openid email profile"
```

## Minting a machine token (Ori agent)

```bash
curl -s -X POST "https://$(terraform output -raw cognito_hosted_ui_domain)/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "$(terraform output -raw cognito_agent_client_id):$(terraform output -raw cognito_agent_client_secret)" \
  -d "grant_type=client_credentials&scope=tnwks-api/read tnwks-api/write"
```

## Switching to a custom domain (`auth.tnwks.us`)

1. Provision an ACM cert in **us-east-1** for `auth.tnwks.us`.
2. Replace `aws_cognito_user_pool_domain.tnwks_auth` with:
   ```hcl
   resource "aws_cognito_user_pool_domain" "tnwks_auth" {
     domain          = "auth.tnwks.us"
     certificate_arn = "<acm cert arn>"
     user_pool_id    = aws_cognito_user_pool.tnwks_auth.id
   }
   ```
3. Add the CloudFront alias record Cognito returns to Cloudflare DNS.
