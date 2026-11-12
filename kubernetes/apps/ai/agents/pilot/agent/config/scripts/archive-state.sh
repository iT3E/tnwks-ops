#!/bin/sh
# The archive step: an init container runs it at each start of the agent pod,
# before doctor and the product. The Recreate strategy stopped the old pod,
# so no process holds the state. It makes one archive of the state directory,
# named with the release of the image that starts and the time, reads it back
# against its checksum, and keeps each archive for 14 days. The first archive
# with the name of a release holds the state from before that release. A
# failed step stops the start of the pod.
set -eu

STATE=/home/node/.openclaw
DEST=/home/node/.openclaw-archives/start
KEEP_DAYS=14

mkdir -p "$DEST"
chmod 0700 "$DEST"

release=$(node /app/openclaw.mjs --version 2>/dev/null | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n 1 || true)
[ -n "$release" ] || release=unknown
name="openclaw-state-$release-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"

# The cache is not state: it comes back on its own.
tar -czf "$DEST/$name.partial" --exclude=./.cache -C "$STATE" .
mv "$DEST/$name.partial" "$DEST/$name"
(cd "$DEST" && sha256sum "$name" > "$name.sha256")

# Read it back.
(cd "$DEST" && sha256sum -c "$name.sha256" > /dev/null)
tar -tzf "$DEST/$name" > /dev/null
echo "archive-state result=ok release=$release name=$name bytes=$(wc -c < "$DEST/$name")"

find "$DEST" -maxdepth 1 -type f \( -name 'openclaw-state-*.tar.gz' -o -name 'openclaw-state-*.tar.gz.sha256' \) -mtime +"$KEEP_DAYS" \
  -printf 'archive-state removed=%f\n' -delete
find "$DEST" -maxdepth 1 -type f -name '*.partial' -delete
