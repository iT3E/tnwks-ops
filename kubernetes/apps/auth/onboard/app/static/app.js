// Browser-only PKCE helpers + session check shared by index.html and
// callback.html. Modern browsers only — uses crypto.subtle and fetch.

const SESSION_KEYS = {
  verifier: "onboard.pkce_verifier",
  state: "onboard.pkce_state",
  idToken: "onboard.id_token",
  accessToken: "onboard.access_token",
  refreshToken: "onboard.refresh_token",
  tokenExpiresAt: "onboard.token_expires_at",
};

function cfg() {
  if (!window.ONBOARD_CONFIG) {
    throw new Error("ONBOARD_CONFIG missing — config.js failed to load");
  }
  return window.ONBOARD_CONFIG;
}

function base64UrlEncode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    bin += String.fromCharCode(bytes[i]);
  }
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomBytes(n) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);
  return a;
}

async function sha256(input) {
  const enc = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", enc);
  return new Uint8Array(hash);
}

async function generatePkce() {
  const verifier = base64UrlEncode(randomBytes(32));
  const challenge = base64UrlEncode(await sha256(verifier));
  const state = base64UrlEncode(randomBytes(16));
  return { verifier, challenge, state };
}

function hasValidSession() {
  const token = sessionStorage.getItem(SESSION_KEYS.accessToken);
  if (!token) return false;
  const expRaw = sessionStorage.getItem(SESSION_KEYS.tokenExpiresAt);
  if (!expRaw) return false;
  const exp = parseInt(expRaw, 10);
  if (Number.isNaN(exp)) return false;
  return Date.now() < exp - 30_000;
}

async function startAuthRedirect() {
  const { verifier, challenge, state } = await generatePkce();
  sessionStorage.setItem(SESSION_KEYS.verifier, verifier);
  sessionStorage.setItem(SESSION_KEYS.state, state);
  const c = cfg();
  const url = new URL(`https://${c.cognitoDomain}/oauth2/authorize`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", c.clientId);
  url.searchParams.set("redirect_uri", c.redirectUri);
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", state);
  window.location.assign(url.toString());
}

async function exchangeCodeForTokens(code) {
  const c = cfg();
  const verifier = sessionStorage.getItem(SESSION_KEYS.verifier);
  if (!verifier) {
    throw new Error(
      "Missing PKCE verifier in this browser. Please start over from the home page.",
    );
  }
  const body = new URLSearchParams();
  body.set("grant_type", "authorization_code");
  body.set("client_id", c.clientId);
  body.set("code", code);
  body.set("redirect_uri", c.redirectUri);
  body.set("code_verifier", verifier);

  const res = await fetch(`https://${c.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = j.error_description || j.error || JSON.stringify(j);
    } catch (_) {
      detail = await res.text();
    }
    throw new Error(`Token exchange failed (${res.status}): ${detail}`);
  }
  const tok = await res.json();
  if (tok.access_token)
    sessionStorage.setItem(SESSION_KEYS.accessToken, tok.access_token);
  if (tok.id_token) sessionStorage.setItem(SESSION_KEYS.idToken, tok.id_token);
  if (tok.refresh_token)
    sessionStorage.setItem(SESSION_KEYS.refreshToken, tok.refresh_token);
  if (tok.expires_in) {
    const exp = Date.now() + parseInt(tok.expires_in, 10) * 1000;
    sessionStorage.setItem(SESSION_KEYS.tokenExpiresAt, String(exp));
  }
  sessionStorage.removeItem(SESSION_KEYS.verifier);
  sessionStorage.removeItem(SESSION_KEYS.state);
}

function passkeyAddUrl() {
  const c = cfg();
  const url = new URL(`https://${c.cognitoDomain}/passkeys/add`);
  url.searchParams.set("client_id", c.clientId);
  url.searchParams.set("redirect_uri", "https://onboard.tnwks.us/done");
  return url.toString();
}

function logoutUrl() {
  const c = cfg();
  const url = new URL(`https://${c.cognitoDomain}/logout`);
  url.searchParams.set("client_id", c.clientId);
  url.searchParams.set("logout_uri", "https://onboard.tnwks.us/");
  return url.toString();
}

function clearSession() {
  Object.values(SESSION_KEYS).forEach((k) => sessionStorage.removeItem(k));
}

window.Onboard = {
  hasValidSession,
  startAuthRedirect,
  exchangeCodeForTokens,
  passkeyAddUrl,
  logoutUrl,
  clearSession,
};
