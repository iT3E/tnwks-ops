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
# The list: {"jobs": [{"key", "cron", "message", "enabled"}, ...]}. A list
# that is not there (no file) declares nothing, and the script changes
# nothing. A list that the script can't read or parse, or with one bad
# entry, changes nothing either: the pass stops with a failed line, so a
# broken file never removes a job. {"jobs": []} is a list with no job, and
# removes each "flux:" job.
#
# On and off ("enabled" of an entry). Git decides, in one way:
# - "enabled": false: the job is off. The sync turns it off at each pass.
# - "enabled": true: the sync turns the job on when it is made, and when the
#   value in git changes from false to true. Between two such changes, a job
#   that is off (the owner turned it off in a chat, or the scheduler did
#   after errors) stays off: its line says state=stopped. To turn it on
#   again: by hand (`openclaw automations enable`), or false and then true
#   in git.
# The value of git of the last pass is in the description of the job
# ("git enabled=true|false"), so the sync sees a change. A chat can edit
# that field, so the sync never turns on a job that is there, is off, and
# has no value it can read: it holds as a stop of the owner, and its line
# says "state=stopped note=no-git-value" once (the sync then writes the
# value). To turn on a job, the sync first writes "true" and then enables
# it; when the enable fails, it writes "false" again, so the next pass tries
# again. When that write fails too, the job stays off (stopped), never on.
#
# Each job of the list: an agent turn of the agent "main" in a new session
# ("isolated"), at the time of the cron expression in the zone TZ of this
# container, and its answer goes to the owner's chat (the one id of
# TELEGRAM_ALLOW_FROM). The message of the job is in git, so it is neutral:
# it names a file of the workspace, and the owner writes the task in that
# file.
#
# At the start it waits for the gateway on loopback, at most WAIT seconds.
# Then it does the sync, and again each EVERY seconds, so a change made in a
# chat goes back to git within EVERY seconds. A pass that fails comes again
# after RETRY seconds, then after twice as long each time, up to EVERY: at a
# start of the pod the gateway answers on loopback some seconds before it
# takes a change of a job (OpenClaw 2026.11.10: "Agent main has not
# completed startup inspection"), so the first pass can fail. A change of the list starts
# the pod again (the ConfigMap gets a new name, so the pod template
# changes), so it is in at once. The CLI output can hold the
# chat id and the message, so it goes nowhere. The lines:
# - one for each job: "automations-sync key=<key> result=ok
#   state=on|off|stopped", "... result=removed" or "... result=failed";
# - one for each pass: "automations-sync result=ok jobs=<n>", or
#   "automations-sync result=failed reason=<reason>" (bad-list, list, job,
#   connect, no-zone, no-chat). A missing zone or chat changes nothing, and
#   its line comes again each EVERY seconds until the pod starts with it.
#   The alert AgentAutomationsSyncFailed reads this line
#   (docs/apps/agent-platform.md, "Alerts");
# - "automations-sync result=none" when there is no list.
set -u

LIST=/home/node/config/automations.json
WAIT=600
EVERY=3600
RETRY=60
PREFIX="flux:"

oc() { timeout 60 node /app/openclaw.mjs "$@"; }

idle() {
  # No exit: a restart loop would add nothing, and the pod stays up.
  while true; do sleep 3600; done
}

# The same, with the failed line again at each pass, so the alert holds
# until the fault is fixed.
fail_each_pass() {
  while true; do
    echo "automations-sync result=failed reason=$1"
    sleep "$EVERY"
  done
}

if [ ! -e "$LIST" ]; then
  echo "automations-sync result=none"
  idle
fi
if [ -z "${TZ:-}" ]; then
  fail_each_pass no-zone
fi
if [ -z "${TELEGRAM_ALLOW_FROM:-}" ]; then
  fail_each_pass no-chat
fi

# The jobs of the list, one per line: key, cron, message (base64, so a space
# or a quote is safe in the shell) and enabled. It exits 1, and prints
# nothing, when the file can't be read or parsed, has no "jobs" list, or has
# one bad entry or a key twice.
declared() {
  node -e '
    const b = (s) => Buffer.from(String(s), "utf8").toString("base64");
    let l;
    try { l = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")); } catch (e) { process.exit(1); }
    if (!l || !Array.isArray(l.jobs)) process.exit(1);
    const out = [], seen = new Set();
    for (const j of l.jobs) {
      if (!j || typeof j !== "object" || !/^[a-z0-9-]+$/.test(j.key || "") || seen.has(j.key)
          || typeof j.cron !== "string" || !j.cron.trim() || typeof j.message !== "string"
          || !j.message.trim() || typeof j.enabled !== "boolean") process.exit(1);
      seen.add(j.key);
      out.push([j.key, b(j.cron), b(j.message), String(j.enabled)].join(" "));
    }
    if (out.length) console.log(out.join("\n"));' "$LIST"
}

# The jobs of the product with the prefix, one per line: "<key without
# prefix> <id> <enabled> <git value of the last pass, or none>", from the
# JSON of the list on stdin.
existing() {
  node -e '
    const o = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const p = process.argv[1];
    for (const j of o.jobs || []) {
      const k = j.declarationKey || "";
      if (!k.startsWith(p)) continue;
      const m = /^git enabled=(true|false)$/.exec(j.description || "");
      console.log([k.slice(p.length), j.id, j.enabled === true, m ? m[1] : "none"].join(" "));
    }' "$PREFIX"
}

dec() { printf %s "$1" | base64 -d; }

sync() {
  if ! list=$(declared); then
    echo "automations-sync result=failed reason=bad-list"
    return
  fi
  if ! out=$(oc automations list --all --json 2>/dev/null) || ! have=$(printf '%s\n' "$out" | existing); then
    echo "automations-sync result=failed reason=list"
    return
  fi
  keys=" "
  jobs=0
  failed=0
  # Each line: a key and base64 fields, so the word split is safe.
  for line in $(printf '%s\n' "$list" | tr ' ' ','); do
    key=${line%%,*}
    rest=${line#*,}
    cron=$(dec "${rest%%,*}")
    rest=${rest#*,}
    message=$(dec "${rest%%,*}")
    enabled=${rest#*,}
    keys="$keys$key "
    jobs=$((jobs + 1))
    # "<id> <enabled> <git value>" of the job, or nothing for a new one.
    old=$(printf '%s\n' "$have" | awk -v k="$key" '$1 == k { print $2, $3, $4 }')
    set -- --agent main --declaration-key "$PREFIX$key" --name "$key" \
      --description "git enabled=$enabled" --cron "$cron" --tz "$TZ" --session isolated \
      --message "$message" --announce --channel telegram --to "$TELEGRAM_ALLOW_FROM" --json
    if [ -z "$old" ] && [ "$enabled" = false ]; then set -- "$@" --disabled; fi
    if ! made=$(oc automations add "$@" 2>/dev/null) \
      || ! now=$(printf '%s\n' "$made" | node -e '
          const j = JSON.parse(require("fs").readFileSync(0, "utf8")).job;
          console.log(j.id + " " + (j.enabled === true));' 2>/dev/null); then
      echo "automations-sync key=$key result=failed"
      failed=$((failed + 1))
      continue
    fi
    id=${now%% *}
    on=${now#* }
    was=${old##* }
    state=on
    note=
    if [ "$enabled" = false ]; then
      state=off
      if [ "$on" = true ] && ! oc automations disable "$id" > /dev/null 2>&1; then state=failed; fi
    elif [ "$on" = false ]; then
      if [ -n "$old" ] && [ "$was" = true ]; then
        state=stopped
      elif [ -n "$old" ] && [ "$was" = none ]; then
        # No value to read (a chat edited the description): a stop of the
        # owner. The edit below writes the value, so this line comes once.
        state=stopped
        note=" note=no-git-value"
      elif ! oc automations edit "$id" --description "git enabled=true" > /dev/null 2>&1; then
        state=failed
      elif ! oc automations enable "$id" > /dev/null 2>&1; then
        # Back to false, so the next pass tries again. If this fails too,
        # the job stays off: a stop, never a job on by mistake.
        oc automations edit "$id" --description "git enabled=false" > /dev/null 2>&1
        state=failed
      else
        was=true
      fi
    fi
    # An add keeps the description of a job that is there, so the git value
    # of this pass goes in with an edit (a display field: it does not stop a
    # run). A turn-on wrote it already, before the enable.
    if [ "$state" != failed ] && [ "$was" != "$enabled" ] \
      && ! oc automations edit "$id" --description "git enabled=$enabled" > /dev/null 2>&1; then
      state=failed
    fi
    if [ "$state" = failed ]; then
      echo "automations-sync key=$key result=failed"
      failed=$((failed + 1))
    else
      echo "automations-sync key=$key result=ok state=$state$note"
    fi
  done
  # A key is [a-z0-9-] and an id is a UUID, so the word split is safe.
  for line in $(printf '%s\n' "$have" | awk 'NF { print $1 "," $2 }'); do
    key=${line%%,*}
    id=${line#*,}
    case "$keys" in
      *" $key "*) ;;
      *)
        if oc automations rm "$id" --json > /dev/null 2>&1; then
          echo "automations-sync key=$key result=removed"
        else
          echo "automations-sync key=$key result=failed"
          failed=$((failed + 1))
        fi
        ;;
    esac
  done
  if [ "$failed" -gt 0 ]; then
    echo "automations-sync result=failed reason=job jobs=$jobs failed=$failed"
  else
    echo "automations-sync result=ok jobs=$jobs"
  fi
}

deadline=$(($(date +%s) + WAIT))
until oc automations status > /dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "automations-sync result=failed reason=connect"
    deadline=$(($(date +%s) + EVERY))
  fi
  sleep 10
done

# The wait after a failed pass: RETRY, twice as long each time, at most EVERY.
first_pause() { if [ "$RETRY" -lt "$EVERY" ]; then echo "$RETRY"; else echo "$EVERY"; fi; }
pause=$(first_pause)
while true; do
  lines=$(sync)
  printf '%s\n' "$lines"
  case "$(printf '%s\n' "$lines" | tail -n 1)" in
    "automations-sync result=failed"*)
      sleep "$pause"
      pause=$((pause * 2))
      if [ "$pause" -gt "$EVERY" ]; then pause=$EVERY; fi
      ;;
    *)
      pause=$(first_pause)
      sleep "$EVERY"
      ;;
  esac
done
