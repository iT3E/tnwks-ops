#!/bin/bash
# tnwks-aruba-backup — save the Aruba S2500 (SW03) running-config to the Google
# Drive sync folder whenever it changes. The switch isn't managed as code, so
# this is its only backup (docs/operations/backups.md). ArubaOS masks the
# enable secret, but the file still holds the admin password hash and the SNMP
# community, so the folder is as sensitive as the other break-glass files.
#
# Credentials: /etc/tnwks/aruba.env (mode 600), written by
# `task wsl-host:aruba-backup` from infrastructure/aruba/secrets.sops.yaml.
set -euo pipefail

DEST="${DEST:-/mnt/g/My Drive/Backup/aruba}"
KEEP="${KEEP:-60}"
# shellcheck disable=SC1091
source /etc/tnwks/aruba.env

if ! mountpoint -q /mnt/g; then
  sudo mkdir -p /mnt/g
  sudo mount -t drvfs G: /mnt/g -o "uid=$(id -u),gid=$(id -g)"
fi
mkdir -p "$DEST"

tmp=$(mktemp)
trap 'rm -f "$tmp" "$tmp.cfg"' EXIT

# ArubaOS 7.4 only speaks legacy SSH algorithms. ssh exits non-zero even on
# success (the switch drops the session on `exit`), so completeness is judged
# by the config ending in `end` below, not by ssh's status.
SSHPASS="$ARUBA_PASSWORD" sshpass -e ssh -tt -o StrictHostKeyChecking=accept-new \
  -o LogLevel=ERROR -o ConnectTimeout=15 \
  -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa -o Ciphers=aes128-cbc,aes256-cbc \
  "$ARUBA_USER@$ARUBA_HOST" >"$tmp" <<EOS || true
enable
$ARUBA_PASSWORD
no paging
show running-config
exit
exit
EOS

# Keep only the config body, from the header comment to `end`.
tr -d '\r' <"$tmp" | sed -n '/^# Configuration file for ArubaOS/,/^end$/p' >"$tmp.cfg"
if ! grep -q '^end$' "$tmp.cfg"; then
  echo "running-config incomplete ($(wc -l <"$tmp.cfg") lines), not saving" >&2
  exit 1
fi

latest=$(ls -1t "$DEST"/sw03-*.cfg 2>/dev/null | head -1 || true)
if [[ -n "$latest" ]] && cmp -s "$tmp.cfg" "$latest"; then
  echo "unchanged since $(basename "$latest")"
else
  out="$DEST/sw03-$(date +%Y%m%d-%H%M).cfg"
  cp "$tmp.cfg" "$out"
  echo "saved $(basename "$out") ($(wc -l <"$out") lines)"
fi

ls -1t "$DEST"/sw03-*.cfg | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -- "$old"
  echo "pruned $(basename "$old")"
done
