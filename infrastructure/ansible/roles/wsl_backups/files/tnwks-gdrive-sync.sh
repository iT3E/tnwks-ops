#!/bin/bash
# tnwks-gdrive-sync — copy what's new on nfs-server's /gdrive-sync export into
# the Google Drive folder G:\My Drive\Backup\nfs-gdrive-sync, and keep the
# newest $KEEP files per subfolder there (docs/operations/backups.md).
#
# Least privilege: this unit is the only thing that touches the Drive, and it
# mounts only that one folder, plus the export read-only, both inside the
# unit's own mount namespace (PrivateMounts), so neither mount outlives the
# run or is visible to anything else. Writers (cluster jobs, e.g. UniFi's
# unifi-gdrive-copy) only ever see the export.
set -euo pipefail

DRIVE_FOLDER='G:\My Drive\Backup\nfs-gdrive-sync'
EXPORT="${EXPORT:-10.5.0.3:/gdrive-sync}"
KEEP="${KEEP:-30}"
D=/run/tnwks-gdrive-sync

mkdir -p "$D/drive" "$D/nfs"
cleanup() { umount "$D/nfs" 2>/dev/null || true; umount "$D/drive" 2>/dev/null || true; }
trap cleanup EXIT

mount -t drvfs "$DRIVE_FOLDER" "$D/drive" -o umask=077
mount -t nfs4 -o ro,soft,timeo=150,retrans=2 "$EXPORT" "$D/nfs"

# New files only: a copy that's already in the Drive is never rewritten.
copied=0
while IFS= read -r -d '' f; do
  rel=${f#"$D/nfs/"}
  [[ -e "$D/drive/$rel" ]] && continue
  mkdir -p "$(dirname "$D/drive/$rel")"
  cp --preserve=timestamps "$f" "$D/drive/$rel.part"
  mv "$D/drive/$rel.part" "$D/drive/$rel"
  echo "copied $rel"
  copied=$((copied + 1))
done < <(find "$D/nfs" -type f ! -name '*.part' -print0)
echo "$copied new file(s)"

# Rotation per subfolder (unifi/, ...): keep the newest $KEEP.
find "$D/drive" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' dir; do
  find "$dir" -maxdepth 1 -type f -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- |
    while IFS= read -r old; do rm -- "$old"; echo "pruned ${old#"$D/drive/"}"; done
done
