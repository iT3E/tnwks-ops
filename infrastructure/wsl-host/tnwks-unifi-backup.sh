#!/bin/bash
# tnwks-unifi-backup — copy the UniFi controller's newest auto-backup off the
# desktop's disk, to the Google Drive for desktop sync folder (G:).
#
# The controller (networking/unifi) writes a .unf daily at 02:00 to its
# local-path PVC, which is on this same disk. This runs at 02:30 from
# tnwks-unifi-backup.timer and keeps the newest $KEEP copies. A .unf holds the
# Wi-Fi passphrase and the APs' device keys. See docs/network/unifi.md.
set -euo pipefail

DEST="${DEST:-/mnt/g/My Drive/Backup/unifi}"
KEEP="${KEEP:-30}"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config-homelab-wsl}"

# Google Drive mounts its virtual drive after WSL starts, so /mnt/g is often an
# empty automount; mount it ourselves.
if ! mountpoint -q /mnt/g; then
  sudo mkdir -p /mnt/g
  sudo mount -t drvfs G: /mnt/g -o "uid=$(id -u),gid=$(id -g)"
fi
mkdir -p "$DEST"

latest=$(kubectl -n networking exec unifi-0 -c app -- \
  sh -c 'ls -1t /config/data/backup/autobackup/*.unf 2>/dev/null | head -1')
if [[ -z "$latest" ]]; then
  echo "no auto-backup on the controller yet"
  exit 0
fi

name=$(basename "$latest")
if [[ -f "$DEST/$name" ]]; then
  echo "already copied: $name"
else
  kubectl -n networking exec unifi-0 -c app -- cat "$latest" > "$DEST/$name.part"
  mv "$DEST/$name.part" "$DEST/$name"
  echo "copied $name ($(stat -c %s "$DEST/$name") bytes)"
fi

# Rotation: keep the newest $KEEP.
ls -1t "$DEST"/*.unf | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -- "$old"
  echo "pruned $(basename "$old")"
done
