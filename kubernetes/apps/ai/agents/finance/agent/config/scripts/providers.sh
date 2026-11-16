#!/bin/sh
# The providers job of the finance agent (CronJob agent-providers,
# ../../app/providers.yaml): the credential of each tool of a sandbox goes
# into the store of the sandbox gateway of this trust domain, and never into
# a sandbox (docs/apps/agent-platform.md, "The finance agent"). The steps of
# the providers job of the workflow agents, for one provider:
#
#   1. checks that the gateway answers (profile list);
#   2. imports each profile of PROFILES that is not in the store, and updates
#      each one whose file changed (the sha256 of the file is the annotation
#      agents.home.arpa/source-sha256 of the stored profile; an update sends
#      the resource_version of the stored profile, which the gateway needs);
#   3. updates (or creates) the provider firefly with FIREFLY_TOKEN.
#
# The credential is an env var of the CLI (--credential NAME, an env lookup),
# so no value is on a command line or in the log. Each failure logs one line,
# "agent-providers result=failed step=<step> name=<name>", and exits 1 (2 for
# a missing setting); a good run logs "agent-providers result=ok".
set -u

ENDPOINT=$(cat "$(dirname "$0")/sandbox-gateway-endpoint" 2>/dev/null)
PROFILES=${PROFILES:-/profiles}
TLS=${TLS:-/openshell-tls}
KEY=agents.home.arpa/source-sha256

fail() {
  echo "agent-providers result=failed step=$1 name=$2"
  exit "${3:-1}"
}

[ -n "$ENDPOINT" ] || fail config sandbox-gateway-endpoint 2
[ -n "${FIREFLY_TOKEN:-}" ] || fail config FIREFLY_TOKEN 2

# The CLI reads the client certificate from the directory of the endpoint.
mtls="$HOME/.config/openshell/gateways/$(printf '%s' "$ENDPOINT" | sed 's#[:/]#_#g')/mtls"
if ! { mkdir -p "$mtls" && cp "$TLS/ca.crt" "$TLS/tls.crt" "$TLS/tls.key" "$mtls/" && chmod 0600 "$mtls"/*; }; then
  fail config client-certificate 2
fi

osh() {
  timeout 60 openshell --gateway-endpoint "$ENDPOINT" "$@"
}

osh profile list > /dev/null || fail gateway "$ENDPOINT"

work=$(mktemp -d)
for f in "$PROFILES"/*.yaml; do
  [ -f "$f" ] || continue
  id=$(sed -n 's/^id: *//p' "$f" | head -n 1)
  [ -n "$id" ] || fail profile-import "$(basename "$f")"
  sum=$(sha256sum "$f" | cut -d ' ' -f 1)
  if ! osh profile describe "$id" > /dev/null 2>&1; then
    { cat "$f"; printf 'annotations:\n  %s: "%s"\n' "$KEY" "$sum"; } > "$work/$id.yaml"
    osh profile import -f "$work/$id.yaml" > /dev/null || fail profile-import "$id"
    echo "profile $id: imported"
    continue
  fi
  # "<sha256 or -> <resource_version>" of the stored profile.
  stored=$(osh profile export "$id" -o json | node -e '
    const p = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const v = Number(p.resource_version || 0);
    if (!Number.isInteger(v)) process.exit(1);
    process.stdout.write(((p.annotations || {})[process.argv[1]] || "-") + " " + v);
  ' "$KEY") || fail profile-export "$id"
  if [ "${stored%% *}" = "$sum" ]; then
    echo "profile $id: unchanged"
    continue
  fi
  { cat "$f"; printf 'annotations:\n  %s: "%s"\nresource_version: %s\n' "$KEY" "$sum" "${stored#* }"; } > "$work/$id.yaml"
  osh profile update -f "$work/$id.yaml" "$id" > /dev/null || fail profile-update "$id"
  echo "profile $id: updated"
done

if osh provider get firefly > /dev/null 2>&1; then
  osh provider update firefly --credential FIREFLY_TOKEN > /dev/null || fail provider-update firefly
  echo "provider firefly: updated"
else
  osh provider create --name firefly --type finance-firefly --credential FIREFLY_TOKEN > /dev/null \
    || fail provider-create firefly
  echo "provider firefly: created"
fi
echo "agent-providers result=ok"
