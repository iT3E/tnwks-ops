#!/usr/bin/env bash
# invoke-elevated.sh <script.ps1> [args...]
#
# Run one of this repo's PowerShell scripts elevated on the Windows host (one
# UAC prompt), wait for it, print its output, and exit with its status. For
# the `wsl-host:*` tasks. Arguments are passed through unquoted, so keep them
# free of spaces and quotes.
set -euo pipefail

script="$1"
shift
win_script="$(wslpath -w "$script")"
win_tmp="$(powershell.exe -NoProfile -Command '[IO.Path]::GetTempPath()' | tr -d '\r')"
win_log="${win_tmp}tnwks-elevated-$$.log"

set +e
powershell.exe -NoProfile -Command "\$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','& ''${win_script}'' $* *> ''${win_log}''; exit \$LASTEXITCODE'; exit \$p.ExitCode"
rc=$?
set -e

log="$(wslpath "$win_log")"
if [[ -f "$log" ]]; then
  # Windows PowerShell writes redirected output as UTF-16.
  iconv -f UTF-16 -t UTF-8 "$log" | tr -d '\r'
  rm -f "$log"
fi
exit "$rc"
