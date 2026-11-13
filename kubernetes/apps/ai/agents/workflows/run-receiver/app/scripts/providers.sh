#!/bin/sh
# The providers of the workflow agents, in the store of the sandbox gateway
# of this trust domain (docs/apps/agent-platform.md, "The run receiver").
# The CronJob workflow-providers runs it: it imports or updates each profile
# of /profiles, then creates or updates each provider with its credential.
# Each credential comes from an env var of the pod (`--credential NAME`, an
# env lookup), so no value is on a command line or in the log.
set -eu

dir="$HOME/.config/openshell/gateways/workflows/mtls"
mkdir -p "$dir"
cp /mtls/ca.crt /mtls/tls.crt /mtls/tls.key "$dir/"
chmod 0600 "$dir"/*
osh() { openshell --color never -g workflows "$@"; }

openshell --color never gateway add "$OPENSHELL_GATEWAY_URL" --local --name workflows >/dev/null

for f in /profiles/*.yaml; do
  id="$(sed -n 's/^id: *//p' "$f")"
  osh profile lint -f "$f" >/dev/null
  if osh profile describe "$id" >/dev/null 2>&1; then
    osh profile update -f "$f" "$id" >/dev/null
    echo "profile $id: updated"
  else
    osh profile import -f "$f" >/dev/null
    echo "profile $id: imported"
  fi
done

# name, profile, the env var of the credential
provider() {
  if osh provider get "$1" >/dev/null 2>&1; then
    osh provider update "$1" --credential "$3" >/dev/null
    echo "provider $1: updated"
  else
    osh provider create --name "$1" --type "$2" --credential "$3" >/dev/null
    echo "provider $1: created"
  fi
}
provider model-gateway workflow-model-gateway MODEL_GATEWAY_KEY
provider github-reviewer workflow-github-reviewer GITHUB_TOKEN
