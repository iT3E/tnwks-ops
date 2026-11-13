#!/bin/sh
# The providers of the workflow agents, in the store of the sandbox gateway
# of this trust domain (docs/apps/agent-platform.md, "The run receiver").
# The CronJob workflow-providers runs it every 15 minutes. Each run:
#
#   1. checks that the gateway answers;
#   2. imports each profile of /profiles that is not in the store, and
#      updates each one whose file changed in git (it keeps the sha256 of the
#      file in the annotation workflow-agents/source-sha256 of the profile);
#   3. updates (or creates) model-gateway with MODEL_GATEWAY_KEY;
#   4. gets a new installation token of the GitHub App of the reviewer
#      (github_app_token.py);
#   5. updates (or creates) github-reviewer with that token.
#
# A failure that a new token can't fix (the gateway, a profile, the model
# provider) stops the run before the token call. Each failure logs one line,
#   workflow-providers result=failed step=<step> name=<name>
# with step gateway, profile-export, profile-import, profile-update,
# provider-create, provider-update or github-token. A run that ends well
# logs "workflow-providers result=ok".
#
# No `profile lint`: import and update check the profile themselves, and
# lint fails on a profile that is already in the store (in OpenShell 0.1.7
# and 0.1.8, lint runs the conflict check of import). An update needs the
# resource_version of the stored profile, so it goes through an export.
#
# Each credential is an env var (`--credential NAME`, an env lookup), so no
# value is on a command line or in the log.
set -eu

fail() {
  echo "workflow-providers result=failed step=$1 name=$2" >&2
  exit 1
}

dir="$HOME/.config/openshell/gateways/workflows/mtls"
mkdir -p "$dir"
cp /mtls/ca.crt /mtls/tls.crt /mtls/tls.key "$dir/"
chmod 0600 "$dir"/*
osh() { openshell --color never -g workflows "$@"; }

openshell --color never gateway add "$OPENSHELL_GATEWAY_URL" --local --name workflows >/dev/null
osh profile list >/dev/null || fail gateway "$OPENSHELL_GATEWAY_URL"

# The profile as the gateway gets it: the file from git with the annotation
# and, for an update, the resource_version of the stored profile.
# profile_file <file> <sha256> <resource_version, or 0> <out>
profile_file() {
  python3 - "$@" <<'EOF'
import sys
import yaml

src, digest, version, out = sys.argv[1:5]
with open(src, encoding="utf-8") as f:
    profile = yaml.safe_load(f)
profile.setdefault("annotations", {})["workflow-agents/source-sha256"] = digest
if int(version):
    profile["resource_version"] = int(version)
with open(out, "w", encoding="utf-8") as f:
    yaml.safe_dump(profile, f, sort_keys=False)
EOF
}

# The annotation and the resource_version of a stored profile (JSON on stdin).
stored_meta() {
  python3 -c '
import json, sys
p = json.load(sys.stdin)
print((p.get("annotations") or {}).get("workflow-agents/source-sha256") or "-", int(p.get("resource_version") or 0))
'
}

for f in /profiles/*.yaml; do
  id="$(sed -n 's/^id: *//p' "$f")"
  sum="$(sha256sum "$f" | cut -c1-64)"
  out="/tmp/profile-$id.yaml"
  if ! osh profile describe "$id" >/dev/null 2>&1; then
    profile_file "$f" "$sum" 0 "$out"
    osh profile import -f "$out" >/dev/null || fail profile-import "$id"
    echo "profile $id: imported"
    continue
  fi
  meta="$(osh profile export "$id" -o json | stored_meta)" || fail profile-export "$id"
  # No brace expansions in this file: Flux substitutes them in the ConfigMap.
  if [ "$(echo "$meta" | cut -d' ' -f1)" = "$sum" ]; then
    echo "profile $id: unchanged"
    continue
  fi
  profile_file "$f" "$sum" "$(echo "$meta" | cut -d' ' -f2)" "$out"
  osh profile update -f "$out" "$id" >/dev/null || fail profile-update "$id"
  echo "profile $id: updated"
done

# name, profile, the env var of the credential
provider() {
  if osh provider get "$1" >/dev/null 2>&1; then
    osh provider update "$1" --credential "$3" >/dev/null || fail provider-update "$1"
    echo "provider $1: updated"
  else
    osh provider create --name "$1" --type "$2" --credential "$3" >/dev/null || fail provider-create "$1"
    echo "provider $1: created"
  fi
}
provider model-gateway workflow-model-gateway MODEL_GATEWAY_KEY

# The token goes from standard output into the env, and nowhere else.
GITHUB_TOKEN="$(python3 /scripts/github_app_token.py)" || fail github-token github-reviewer
export GITHUB_TOKEN
provider github-reviewer workflow-github-reviewer GITHUB_TOKEN
echo "workflow-providers result=ok"
