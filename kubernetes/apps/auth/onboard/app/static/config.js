// Public OAuth client config. Client ID is not a secret — it identifies the
// browser-side PKCE client to Cognito and is safe to commit. Updated when
// aws_cognito_user_pool_client.onboard is recreated (TFC apply emits the
// cognito_onboard_client_id output).
window.ONBOARD_CONFIG = {
  cognitoDomain: "auth.tnwks.us",
  clientId: "lh02s535c1ov7s9su7nbs5u1c",
  redirectUri: "https://onboard.tnwks.us/callback",
  rpId: "tnwks.us",
};
