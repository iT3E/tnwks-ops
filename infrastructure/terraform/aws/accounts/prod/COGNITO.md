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
  login allowed *(applied via [terraform_data + AWS CLI](#mfa-configuration)
  due to a provider gap)*
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

## MFA configuration

MFA + WebAuthn passkeys are enabled by `terraform_data.cognito_mfa` in
`cognito.tf`, which calls `aws cognito-idp set-user-pool-mfa-config` via a
local-exec provisioner. The CLI shell-out exists because the AWS provider's
`web_authn_configuration` block is missing the `FactorConfiguration`
argument that AWS requires when WebAuthn is a first-auth-factor — see
[hashicorp/terraform-provider-aws#47598](https://github.com/hashicorp/terraform-provider-aws/issues/47598).

To change MFA settings (relying party ID, user verification, factor
configuration), edit the `local.cognito_mfa` map in `cognito.tf` and apply.
The trigger hash will fire the provisioner again.

When the upstream provider gains `factor_configuration` support, fold the
fields back into `aws_cognito_user_pool` and drop both the
`terraform_data.cognito_mfa` resource and the lifecycle ignores.

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
