#!/bin/sh
# The backup container of the agent pod. Once a day (UTC) it:
#
# 1. runs the backup command of the product with its check, and writes the
#    archive to the archive directory, which is outside the workspace, so no
#    sandbox gets it. It keeps the last 7.
# 2. checks the instruction files of the workspace: it makes a SHA-256 of
#    AGENTS.md, SOUL.md, USER.md, IDENTITY.md and each file under skills/,
#    compares the list with the list of the day before (in the archive
#    directory), and logs each file that is new, changed or gone. An alert
#    reads these lines, and one more fires when no `instruction-check
#    result=` line came in 26 hours.
#
# A failed backup ends the container, so the restart shows on the pod.
set -eu

ARCHIVES=/home/node/.openclaw-archives
DAILY="$ARCHIVES/daily"
LISTS="$ARCHIVES/instruction-files"
WORKSPACE=/home/node/.openclaw/workspace
KEEP=7

mkdir -p "$DAILY" "$LISTS"
chmod 0700 "$DAILY" "$LISTS"

instruction_list() (
  # One line for each file: "<sha256>  <path in the workspace>", sorted.
  [ -d "$WORKSPACE" ] || exit 0
  cd "$WORKSPACE"
  {
    for f in AGENTS.md SOUL.md USER.md IDENTITY.md; do
      if [ -f "$f" ]; then printf '%s\n' "$f"; fi
    done
    if [ -d skills ]; then find skills -type f; fi
  } | LC_ALL=C sort | while IFS= read -r f; do
    sha256sum "$f"
  done
)

instruction_check() {
  today="$1"
  new="$LISTS/$today.sha256"
  instruction_list > "$new.partial"
  mv "$new.partial" "$new"
  count=$(wc -l < "$new")
  # The newest list before today's (a glob expands in sorted order).
  prev=""
  for f in "$LISTS"/????-??-??.sha256; do
    if [ -f "$f" ] && [ "${f##*/}" != "$today.sha256" ]; then prev=${f##*/}; fi
  done
  if [ -z "$prev" ]; then
    echo "instruction-check result=first files=$count"
  else
    # "<change> <file>" for each file that is new, changed or gone. A
    # sha256sum line is 64 hex digits, two spaces, then the file name.
    diff=$(awk '
      FNR == NR { old[substr($0, 67)] = $1; next }
      { cur[substr($0, 67)] = $1 }
      END {
        for (f in old) if (!(f in cur)) print "gone", f
        for (f in cur) if (!(f in old)) print "new", f; else if (old[f] != cur[f]) print "changed", f
      }' "$LISTS/$prev" "$new" | LC_ALL=C sort -k 2)
    changes=0
    if [ -n "$diff" ]; then
      changes=$(printf '%s\n' "$diff" | wc -l)
      printf '%s\n' "$diff" | while read -r change f; do
        echo "instruction-check file=$f change=$change"
      done
    fi
    echo "instruction-check result=ok files=$count changed=$changes since=${prev%.sha256}"
  fi
  # Keep the lists of the last 30 days.
  find "$LISTS" -maxdepth 1 -type f -name '*.sha256' -mtime +30 -delete
}

while true; do
  today=$(date -u +%Y-%m-%d)
  out="$DAILY/openclaw-backup-$today.tar.gz"
  if [ ! -f "$out" ]; then
    if ! node /app/openclaw.mjs backup create --verify --output "$out"; then
      echo "daily-backup result=failed day=$today"
      rm -f "$out"
      exit 1
    fi
    echo "daily-backup result=ok day=$today bytes=$(wc -c < "$out")"
    # Keep the last 7 (a glob expands in sorted order, oldest first).
    set -- "$DAILY"/openclaw-backup-????-??-??.tar.gz
    while [ "$#" -gt "$KEEP" ]; do
      rm -f "$1"
      echo "daily-backup removed=${1##*/}"
      shift
    done
    instruction_check "$today"
  fi
  sleep 3600
done
