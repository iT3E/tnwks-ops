#!/usr/bin/env bash
# Writes a placeholder flux-system/cluster-secrets for flate to stdout, with
# every key name of kubernetes/clusters/*/cluster-secrets.sops.yaml.
#
# Since flate 0.7 SOPS-encrypted values are unreadable, so the ${VAR}s Flux
# substitutes from cluster-secrets render empty and fail chart schemas (a
# null oauth2-proxy cookieSecret, a null valkey arg). flate's --supply
# (FLATE_SUPPLY) fills the keys the repository can't provide; a supplied
# object never renders as output. Only key names are read here, never
# values, and the values written are placeholders.
#
# Usage: .github/flate/supply.sh > FILE   (from the repository root)
set -euo pipefail

keys="$(sed -nE 's/^[[:space:]]+([A-Z][A-Z0-9_]*): ENC\[.*/\1/p' \
  kubernetes/clusters/*/cluster-secrets.sops.yaml | sort -u)"
[[ -n "${keys}" ]] || { echo "no cluster-secrets keys found" >&2; exit 1; }

printf '%s\n' '---' 'apiVersion: v1' 'kind: Secret' 'metadata:' \
  '  name: cluster-secrets' '  namespace: flux-system' 'stringData:'
while IFS= read -r key; do
  printf '  %s: flate-placeholder\n' "${key}"
done <<<"${keys}"
