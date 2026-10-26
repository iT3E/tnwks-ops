#!/usr/bin/env bash
# issue-cert.sh <common-name> <out-dir> [days]
#
# Issue an IAM Roles Anywhere client certificate from the tnwks-ops CA. The
# client key is generated in <out-dir> and never leaves it; the CA key is
# streamed from SOPS (ca-key.sops.yaml) and never written to disk. The CN must
# be in the role's allowlist (rolesanywhere_sops_common_names in main.tf) to
# get a session.
set -euo pipefail

cn="${1:?usage: issue-cert.sh <common-name> <out-dir> [days]}"
out="${2:?usage: issue-cert.sh <common-name> <out-dir> [days]}"
days="${3:-365}"
here="$(cd "$(dirname "$0")" && pwd)"

[[ "$cn" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || { echo "invalid CN: $cn" >&2; exit 2; }
mkdir -p "$out"
chmod 700 "$out"
umask 077

key="$out/$cn.key"
cert="$out/$cn.pem"
[[ -f "$key" ]] || openssl ecparam -name prime256v1 -genkey -noout -out "$key"

ext="$(mktemp)"
trap 'rm -f "$ext"' EXIT
printf '%s\n' \
  "basicConstraints=critical,CA:FALSE" \
  "keyUsage=critical,digitalSignature" \
  "extendedKeyUsage=clientAuth" \
  "subjectKeyIdentifier=hash" \
  "authorityKeyIdentifier=keyid" >"$ext"

openssl req -new -key "$key" -subj "/O=tnwks-ops/CN=$cn" |
  openssl x509 -req -sha256 -days "$days" \
    -CA "$here/ca.pem" \
    -CAkey <(sops -d --extract '["ca_key"]' "$here/ca-key.sops.yaml") \
    -set_serial "0x$(openssl rand -hex 16)" \
    -extfile "$ext" -out "$cert"

openssl verify -CAfile "$here/ca.pem" "$cert" >/dev/null
echo "issued $cert ($(openssl x509 -in "$cert" -noout -enddate))"
