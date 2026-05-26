# Cognito — `tnwks-auth`

AWS Cognito user pool that backs authentication for internal services at
`*.internal.tnwks.us`. The cluster's oauth2-proxy talks to this pool;
machine-to-machine clients (e.g. the Ori agent) get tokens via
`client_credentials`.

## What's here

| File          | Purpose                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `main.tf`     | Provider + user pool (Essentials tier) + groups                         |
| `clients.tf`  | App clients, `tnwks-api` resource server, hosted UI domain, branding    |
| `variables.tf`| Inputs (region, hosted UI prefix, optional custom domain, RP ID)        |
| `outputs.tf`  | Pool/client IDs + secrets, OIDC issuer endpoint, hosted UI domain       |

## Pool design

- **Tier:** `ESSENTIALS` (free up to 10k MAU, includes passkeys + managed login)
- **Sign-in:** email as username, email auto-verified
- **MFA:** required, with TOTP **and** WebAuthn/passkeys enabled
- **Passwordless:** `sign_in_policy.allowed_first_auth_factors` includes
  `WEB_AUTHN`, so a registered passkey alone is enough to sign in
- **Password policy:** 12+ chars, mixed case, numbers, symbols
- **Token lifetimes:** access 24h, id 24h, refresh 30d
- **Account recovery:** verified email
- **Admin-create only:** users cannot self-sign-up; create them via
  `aws cognito-idp admin-create-user` (see below)

## App clients

### `oauth2-proxy` (authorization_code)
- Callback: `https://oauth2.internal.tnwks.us/oauth2/callback`
- Logout: `https://oauth2.internal.tnwks.us/oauth2/sign_out`
- Scopes: `openid email profile`
- Has client secret

### `agent-ori` (client_credentials)
- Scopes: `tnwks-api/read`, `tnwks-api/write`
- Has client secret
- No callback / hosted UI involvement

## Domain

Defaults to the prefix domain `tnwks-auth.auth.us-east-1.amazoncognito.com`.
To switch to a custom domain (`auth.tnwks.us`):

1. Provision an ACM certificate in **us-east-1** for `auth.tnwks.us`.
2. Set `custom_domain = "auth.tnwks.us"` and `acm_cert_arn = "<arn>"` in the
   workspace.
3. Add the CloudFront alias record Cognito returns to Cloudflare DNS.

## Workspace

Configured for Terraform Cloud workspace `tnwks-ops-aws-cognito` (VCS-driven,
runs after `tnwks-ops-aws-prod`). The provider assumes the same
`tnwks-org-init-role` cross-account role as the prod account stack.

## Operational recipes

### Add a user
```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$(terraform output -raw user_pool_id)" \
  --username alice@example.com \
  --user-attributes Name=email,Value=alice@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
```

### Add a user to a group
```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$(terraform output -raw user_pool_id)" \
  --username alice@example.com \
  --group-name admins   # or viewers, agents
```

### Add a new group
Append a new `aws_cognito_user_group` resource to `main.tf`.

### Wire up oauth2-proxy
- `--provider=oidc`
- `--oidc-issuer-url=$(terraform output -raw user_pool_endpoint)`
- `--client-id=$(terraform output -raw oauth2_proxy_client_id)`
- `--client-secret=$(terraform output -raw oauth2_proxy_client_secret)`
- `--scope="openid email profile"`

### Get a machine token (Ori agent)
```bash
curl -s -X POST "https://$(terraform output -raw hosted_ui_domain)/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "$(terraform output -raw agent_client_id):$(terraform output -raw agent_client_secret)" \
  -d "grant_type=client_credentials&scope=tnwks-api/read tnwks-api/write"
```
