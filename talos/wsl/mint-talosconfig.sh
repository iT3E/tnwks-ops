#!/usr/bin/env bash
# mint-talosconfig.sh [days]
#
# Break-glass for the WSL (Talos-in-Docker) cluster: issue a new os:admin
# talosconfig from the cluster's own CA when no working one exists (lost,
# overwritten by another `talosctl cluster create`, or expired). Nothing on
# the nodes changes; existing credentials keep working.
#
# The CA is read from the control-plane container's machine config into a
# RAM-only directory and wiped on exit. Output:
#   ~/.talos/config-homelab-wsl            (mode 600)
#   talos/wsl/talosconfig.sops.yaml        (SOPS-encrypted copy, commit it)
#
# Talos has no revocation: a minted certificate is valid until it expires
# (default 365 days). Revoking early means `talosctl rotate-ca`.
set -euo pipefail

days="${1:-365}"
container="homelab-wsl-controlplane-1"
node="10.5.0.2"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$HOME/.talos/config-homelab-wsl"

endpoint="$(docker port "$container" 50000/tcp | head -1)"
[ -n "$endpoint" ] || { echo "no Talos API port mapping on $container" >&2; exit 1; }

umask 077
tmp="$(mktemp -d /dev/shm/talosmint.XXXXXX)"
trap 'find "$tmp" -type f -exec shred -u {} + 2>/dev/null; rm -rf "$tmp"' EXIT

docker cp "$container:/system/state/config.yaml" "$tmp/mc.yaml" >/dev/null
/usr/bin/python3 -I - "$tmp" <<'EOF'
import base64, sys, yaml
tmp = sys.argv[1]
mc = next(d for d in yaml.safe_load_all(open(tmp + "/mc.yaml")) if d and "machine" in d)
ca = mc["machine"]["ca"]
open(tmp + "/ca.crt", "wb").write(base64.b64decode(ca["crt"]))
key = base64.b64decode(ca["key"]).replace(b"ED25519 PRIVATE KEY", b"PRIVATE KEY")
open(tmp + "/ca.key", "wb").write(key)
EOF
shred -u "$tmp/mc.yaml"

openssl genpkey -algorithm ed25519 -out "$tmp/admin.key" 2>/dev/null
openssl req -new -key "$tmp/admin.key" -subj "/O=os:admin/CN=$(whoami)-wsl" -out "$tmp/admin.csr" 2>/dev/null
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > "$tmp/ext"
openssl x509 -req -in "$tmp/admin.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" \
  -set_serial "0x$(openssl rand -hex 16)" -days "$days" -extfile "$tmp/ext" \
  -out "$tmp/admin.crt" 2>/dev/null
openssl verify -CAfile "$tmp/ca.crt" "$tmp/admin.crt" >/dev/null

mkdir -p "$(dirname "$out")"
/usr/bin/python3 -I - "$tmp" "$endpoint" "$node" "$out" <<'EOF'
import base64, sys, yaml
tmp, endpoint, node, out = sys.argv[1:]
b64 = lambda f: base64.b64encode(open(tmp + "/" + f, "rb").read()).decode()
cfg = {"context": "homelab-wsl", "contexts": {"homelab-wsl": {
    "endpoints": [endpoint], "nodes": [node],
    "ca": b64("ca.crt"), "crt": b64("admin.crt"), "key": b64("admin.key")}}}
open(out, "w").write(yaml.safe_dump(cfg, sort_keys=False))
EOF
chmod 600 "$out"

talosctl --talosconfig "$out" version --short >/dev/null
echo "talosconfig works: $out (valid $days days)"

(cd /dev/shm && "$repo/bin/sops" -e --input-type yaml --output-type yaml \
  --age age1mzxwncaj0364q8jxfj0y73ptt63czwxucf9lrnn28c7kwyr5wfdsj9gsk2,age12uckcdwv9p2s75ae3h20wgn70taatdzzl44n9x6vr8h9qurzkaqqkhphz7,age1hmmuna95ktwl9sf9j56wjjukyf22ukdyjd8c47nlf5fjgyeyk5rsmhtyjx \
  --kms 'arn:aws:kms:us-west-2:654654220436:key/mrk-74c4597ebffd41d38a852228e13d76cc+arn:aws:iam::654654220436:role/iam-role-sops' \
  "$out") > "$here/talosconfig.sops.yaml"
echo "encrypted copy: talos/wsl/talosconfig.sops.yaml"
