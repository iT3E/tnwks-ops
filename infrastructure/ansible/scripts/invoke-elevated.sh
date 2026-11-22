#!/usr/bin/env bash
# invoke-elevated.sh <script.ps1> [args...]
#
# Run one of this repo's PowerShell scripts elevated on the Windows host (one
# UAC prompt), wait for it, print its output, and exit with its status. For
# the `wsl-host:*` tasks. Arguments are passed through unquoted, so keep them
# free of spaces and quotes.
#
# It fails closed: when the elevated process does not start (the UAC prompt
# is refused, or the session has no interactive desktop) or gives no exit
# code, it prints one line that says so and exits with NOT_RUN, so no task
# can end green on a script that did not run. NOT_RUN is not 2, which the
# -Check form of a script uses for drift.
set -euo pipefail

NOT_RUN=125
MARK="TNWKS-ELEVATED-NOT-RUN:"

script="$1"
shift
win_script="$(wslpath -w "$script")"
win_tmp="$(powershell.exe -NoProfile -Command '[IO.Path]::GetTempPath()' | tr -d '\r')"
win_log="${win_tmp}tnwks-elevated-$$.log"

set +e
# A terminating error escapes `*>`, so it is caught and appended to the log too.
# A start that fails, or a process with no exit code, prints MARK and the
# reason on the output of this outer process, which the lines below read.
outer="$(powershell.exe -NoProfile -Command "\$ErrorActionPreference = 'Stop'; try { \$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','try { & ''${win_script}'' $* *> ''${win_log}'' } catch { \$_ | Out-String | Out-File -Append -Encoding unicode ''${win_log}''; exit 1 }; exit \$LASTEXITCODE' } catch { Write-Output ('${MARK} the elevated process did not start: ' + \$_.Exception.Message); exit ${NOT_RUN} }; if (\$null -eq \$p -or \$null -eq \$p.ExitCode) { Write-Output '${MARK} the elevated process gave no exit code'; exit ${NOT_RUN} }; exit \$p.ExitCode" 2>&1)"
rc=$?
set -e

log="$(wslpath "$win_log")"
if [[ -f "$log" ]]; then
  # Windows PowerShell writes redirected output as UTF-16.
  iconv -f UTF-16 -t UTF-8 "$log" | tr -d '\r'
  rm -f "$log"
fi

outer="$(printf '%s\n' "$outer" | tr -d '\r')"
if grep -qF "$MARK" <<<"$outer"; then
  line="$(grep -F "$MARK" <<<"$outer" | head -n 1)"
  echo "invoke-elevated.sh: ${line#*"$MARK" }; $(basename "$script") did not run" >&2
  exit "$NOT_RUN"
fi
# Anything else that the outer process printed (an error of powershell.exe
# itself) goes to stderr, and its status stands.
if [[ -n "$outer" ]]; then
  printf '%s\n' "$outer" >&2
fi
exit "$rc"
