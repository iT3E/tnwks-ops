#!/bin/bash
# tnwks-windows-drift-check — nightly `--check --diff` of the Windows host
# playbook (playbooks/windows-host.yml) against main, reported to Prometheus.
#
# Runs from tnwks-windows-drift-check.timer. It keeps its own shallow clone of
# main (so it never touches a working checkout), runs `task
# wsl-host:windows-check`, and writes the PLAY RECAP counts for the Windows
# host as a .prom file into windows_exporter's textfile directory over the
# same SSH connection Ansible uses. Prometheus scrapes them with the rest of
# the windows-exporter job; WindowsHostConfigDrift and
# WindowsHostDriftCheckStale (alerts/windows.yaml) fire on them. The full
# output of the last run is in $STATE_DIR/last-check.log and the journal.
# See docs/wsl/windows-ansible.md.
set -uo pipefail

STATE_DIR="${STATE_DIR:-$HOME/.cache/tnwks-ops/windows-drift-check}"
REPO_DIR="${REPO_DIR:-$STATE_DIR/tnwks-ops}"
REPO_URL="${REPO_URL:-https://github.com/iT3E/tnwks-ops.git}"
WINDOWS_SSH_KEY="${WINDOWS_SSH_KEY:-$HOME/.ssh/id_ed25519_winhost}"
TEXTFILE_DIR='C:\Program Files\windows_exporter\textfile_inputs'
PROM_NAME=tnwks_windows_drift_check

mkdir -p "$STATE_DIR"
log="$STATE_DIR/last-check.log"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --quiet --depth 1 --branch main "$REPO_URL" "$REPO_DIR" || exit 1
fi
git -C "$REPO_DIR" fetch --quiet --depth 1 origin main &&
  git -C "$REPO_DIR" checkout --quiet --force --detach FETCH_HEAD || exit 1
commit=$(git -C "$REPO_DIR" rev-parse --short HEAD)

inventory="$REPO_DIR/infrastructure/ansible/inventory/windows.yml"
host=$(yq '.all.children.windows.hosts | keys | .[0]' "$inventory")
address=$(yq ".all.children.windows.hosts.\"$host\".ansible_host" "$inventory")
user=$(yq '.all.children.windows.vars.ansible_user' "$inventory")

started=$(date +%s)
(cd "$REPO_DIR" && task wsl-host:windows-check) >"$log" 2>&1
rc=$?
duration=$(($(date +%s) - started))
cat "$log"

# The Windows host's line of the PLAY RECAP, e.g.
#   DESKTOP-HKE05KI : ok=62 changed=0 unreachable=0 failed=0 skipped=14 ...
recap=$(grep -E "^${host}[[:space:]]+:" "$log" | tail -1)
count() { sed -nE "s/.*[[:space:]]$1=([0-9]+).*/\1/p" <<<"$recap"; }
changed=$(count changed)
failed=$(count failed)
unreachable=$(count unreachable)
# No recap (the playbook didn't get to run): report it as a failure.
[[ -n "$recap" ]] || { changed=0 failed=1 unreachable=0; }

echo "drift check of $host at $commit: rc=$rc changed=$changed failed=$failed unreachable=$unreachable (${duration}s)"

prom=$(
  cat <<EOF
# HELP ${PROM_NAME}_timestamp_seconds When the nightly Ansible --check of this host finished.
# TYPE ${PROM_NAME}_timestamp_seconds gauge
${PROM_NAME}_timestamp_seconds $(date +%s)
# HELP ${PROM_NAME}_exit_code Exit code of task wsl-host:windows-check (0 = clean).
# TYPE ${PROM_NAME}_exit_code gauge
${PROM_NAME}_exit_code $rc
# HELP ${PROM_NAME}_changed Tasks the check would change (drift).
# TYPE ${PROM_NAME}_changed gauge
${PROM_NAME}_changed $changed
# HELP ${PROM_NAME}_failed Failed tasks, including the check-only setting assertions.
# TYPE ${PROM_NAME}_failed gauge
${PROM_NAME}_failed $failed
# HELP ${PROM_NAME}_unreachable 1 when Ansible could not reach the host.
# TYPE ${PROM_NAME}_unreachable gauge
${PROM_NAME}_unreachable $unreachable
# HELP ${PROM_NAME}_duration_seconds How long the check took.
# TYPE ${PROM_NAME}_duration_seconds gauge
${PROM_NAME}_duration_seconds $duration
EOF
)

# Written as a temp file and renamed, so the exporter never reads half a file.
# The script goes over as -EncodedCommand (UTF-16LE base64) and the content as
# base64 inside it, which sidesteps Windows command-line quoting.
ps="\$b = [Convert]::FromBase64String('$(printf '%s\n' "$prom" | base64 -w0)'); \$d = '$TEXTFILE_DIR';"
ps+=" [IO.File]::WriteAllBytes(\"\$d\\$PROM_NAME.prom.tmp\", \$b);"
ps+=" Move-Item -Force \"\$d\\$PROM_NAME.prom.tmp\" \"\$d\\$PROM_NAME.prom\""
enc=$(printf '%s' "$ps" | iconv -t UTF-16LE | base64 -w0)
ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$WINDOWS_SSH_KEY" "$user@$address" \
  "powershell.exe -NoProfile -NonInteractive -EncodedCommand $enc"
push=$?
[[ $push -eq 0 ]] || echo "could not write the metrics file on $host (ssh exit $push)" >&2

# The unit fails when the host has drift or the report didn't arrive, so
# `systemctl --failed` shows it too.
[[ $rc -eq 0 && $push -eq 0 ]]
