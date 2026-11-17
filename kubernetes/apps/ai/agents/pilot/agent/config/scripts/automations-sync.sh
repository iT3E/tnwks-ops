#!/bin/sh
# The automations-sync container of an agent pod. It makes the scheduled jobs
# of the product equal to the list in git (automations.json in the config of
# the agent), so Flux stays the source of the schedule.
#
# The product keeps its jobs in its state database, and only a running
# gateway changes them (its CLI, `openclaw automations`). It has no config
# key for them. Its own declared jobs (the heartbeat, the job of memory-core)
# carry a declaration key: an add with a key that is there already updates
# that job, with the same id. This script uses the same way, with the keys
# "flux:<key>":
# - each job of the list: an add with its key (made, or set back to git);
# - each job of the product with a key "flux:..." that is not in the list:
#   removed. A job with another key, or with none (made in a chat), stays.
#
# Each job of the list: an agent turn of the agent "main" in a new session
# ("isolated"), at the time of the cron expression in the zone TZ of this
# container, and its answer goes to the owner's chat (the one id of
# TELEGRAM_ALLOW_FROM). The message of the job is in git, so it is neutral:
# it names a file of the workspace, and the owner writes the task in that
# file.
#
# An add keeps a job that is off (by the owner, or by the scheduler after
# errors) off, so this script never turns a job on. It reports it:
# "enabled=false" in the line of that job.
#
# At the start it waits for the gateway on loopback, at most WAIT seconds.
# Then it does the sync, and again each EVERY seconds, so a change made in a
# chat goes back to git within EVERY seconds. A change of the list starts
# the pod again (Reloader), so it is in at once. The CLI output can hold the
# chat id and the message, so it goes nowhere. One line for each job:
# "automations-sync key=<key> result=ok|failed|removed [enabled=false]", and
# "automations-sync result=failed reason=connect|list|no-zone|no-chat" when it can
# do nothing. With no list it logs "automations-sync result=none" and does
# nothing.
set -u

LIST=/home/node/config/automations.json
WAIT=600
EVERY=3600
PREFIX="flux:"

oc() { timeout 60 node /app/openclaw.mjs "$@"; }

idle() {
  # No exit: a restart loop would add nothing, and the pod stays up.
  while true; do sleep 3600; done
}

if [ ! -s "$LIST" ]; then
  echo "automations-sync result=none"
  idle
fi
if [ -z "${TZ:-}" ]; then
  echo "automations-sync result=failed reason=no-zone"
  idle
fi
if [ -z "${TELEGRAM_ALLOW_FROM:-}" ]; then
  echo "automations-sync result=failed reason=no-chat"
  idle
fi

# The jobs of the list, one per line: key, cron and message, each base64,
# so a space or a quote in the message is safe in the shell.
declared() {
  node -e '
    const b = (s) => Buffer.from(String(s), "utf8").toString("base64");
    const l = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    for (const j of l.jobs || []) {
      if (!/^[a-z0-9-]+$/.test(j.key || "") || !j.cron || !j.message) {
        console.log("bad " + b(j.key || "?"));
        continue;
      }
      console.log(["job", b(j.key), b(j.cron), b(j.message)].join(" "));
    }' "$LIST"
}

# The jobs of the product with the prefix: "<key without prefix> <id>
# <enabled>", one per line, from the JSON of the list on stdin.
existing() {
  node -e '
    const o = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const p = process.argv[1];
    for (const j of o.jobs || []) {
      const k = j.declarationKey || "";
      if (k.startsWith(p)) console.log(k.slice(p.length) + " " + j.id + " " + j.enabled);
    }' "$PREFIX"
}

dec() { printf %s "$1" | base64 -d; }

sync() {
  if ! out=$(oc automations list --all --json 2>/dev/null); then
    echo "automations-sync result=failed reason=list"
    return
  fi
  have=$(printf '%s\n' "$out" | existing)
  keys=" "
  # Each line: a word and base64 fields, so the word split is safe.
  for line in $(declared | tr ' ' ','); do
    kind=${line%%,*}
    rest=${line#*,}
    if [ "$kind" = bad ]; then
      echo "automations-sync key=$(dec "$rest") result=failed reason=bad-entry"
      continue
    fi
    key=$(dec "${rest%%,*}")
    rest=${rest#*,}
    cron=$(dec "${rest%%,*}")
    message=$(dec "${rest#*,}")
    keys="$keys$key "
    if oc automations add --agent main --declaration-key "$PREFIX$key" --name "$key" \
      --cron "$cron" --tz "$TZ" --session isolated --message "$message" \
      --announce --channel telegram --to "$TELEGRAM_ALLOW_FROM" --json > /dev/null 2>&1; then
      enabled=$(printf '%s\n' "$have" | awk -v k="$key" '$1 == k { print $3 }')
      if [ "$enabled" = false ]; then
        echo "automations-sync key=$key result=ok enabled=false"
      else
        echo "automations-sync key=$key result=ok"
      fi
    else
      echo "automations-sync key=$key result=failed"
    fi
  done
  # A key is [a-z0-9-] and an id is a UUID, so the word split is safe.
  printf '%s\n' "$have" | while read -r key id _; do
    [ -n "$key" ] || continue
    case "$keys" in
      *" $key "*) ;;
      *)
        if oc automations rm "$id" --json > /dev/null 2>&1; then
          echo "automations-sync key=$key result=removed"
        else
          echo "automations-sync key=$key result=failed reason=remove"
        fi
        ;;
    esac
  done
}

deadline=$(($(date +%s) + WAIT))
until oc automations status > /dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "automations-sync result=failed reason=connect"
    deadline=$(($(date +%s) + EVERY))
  fi
  sleep 10
done

while true; do
  sync
  sleep "$EVERY"
done
