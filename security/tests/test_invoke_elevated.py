"""invoke-elevated.sh fails closed (infrastructure/ansible/scripts/README.md).

The helper runs a script of the repo elevated on the Windows host through
`Start-Process -Verb RunAs`. A session with no interactive desktop can not
start that process, and the helper then ended with exit 0, so `task
wsl-host:sshd` could end green on a script that did not run.

The test puts stubs of `powershell.exe` and `wslpath` first on the PATH. The
stub of `powershell.exe` runs the command of the helper with pwsh, after a
function `Start-Process` of the test: the start fails as with no desktop,
the process gives no exit code, or it runs, writes its log as Windows
PowerShell does (UTF-16) and exits with 0, 1 or 2. No Windows host is used.
The last test runs the command lines of `sshd` and `sshd-check` with a stub
of the helper, for each exit code.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from test_wsl_start_unelevated import PWSH, needs_pwsh

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "infrastructure/ansible/scripts/invoke-elevated.sh"
TASKS = ROOT / ".taskfiles/WslHostTasks.yml"
NOT_RUN = 125

WSLPATH = """#!/bin/sh
# The paths of the test are the same on both sides.
[ "$1" = "-w" ] && shift
printf '%s\\n' "$1"
"""

# The command of the helper runs after this prelude, so Start-Process is the
# function of the test, not the cmdlet.
PRELUDE = r"""
function Start-Process {
    param([string]$FilePath, [string]$Verb, [switch]$Wait, [switch]$PassThru, [string[]]$ArgumentList)
    switch -Regex ($env:STUB_START) {
        '^fail$' { Write-Error 'This command requires an interactive window station.'; return }
        '^noexit$' { return [pscustomobject]@{ ExitCode = $null } }
        '^exit (\d+)$' {
            $code = [int]$Matches[1]
            $log = [regex]::Match(($ArgumentList -join ' '), "\*> '([^']+)'").Groups[1].Value
            "the output of the script, exit $code" | Out-File -Encoding unicode $log
            return [pscustomobject]@{ ExitCode = $code }
        }
    }
}
"""

POWERSHELL = """#!/bin/sh
# The stub of powershell.exe: the temp folder, or the command of the helper
# run by pwsh after the prelude of the test.
case "$*" in
  *GetTempPath*) printf '%s/\\r\\n' "$STUB_TMP"; exit 0 ;;
esac
[ "$1" = "-NoProfile" ] && shift
[ "$1" = "-Command" ] && shift
exec "$STUB_PWSH" -NoProfile -NonInteractive -Command "$(cat "$STUB_PRELUDE"); $1"
"""


def run(tmp_path: Path, start: str, extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    for name, text in (("wslpath", WSLPATH), ("powershell.exe", POWERSHELL)):
        (stubs / name).write_text(text)
        (stubs / name).chmod(0o755)
    (tmp_path / "prelude.ps1").write_text(PRELUDE)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "STUB_START": start,
           "STUB_TMP": str(tmp_path / "tmp"), "STUB_PRELUDE": str(tmp_path / "prelude.ps1"),
           "STUB_PWSH": PWSH or "pwsh"}
    return subprocess.run(["bash", str(HELPER), str(tmp_path / "Script.ps1"), *extra],
                          capture_output=True, text=True, env=env, timeout=120, check=False)


@needs_pwsh
def test_a_start_that_fails_fails_the_helper_with_one_line(tmp_path):
    result = run(tmp_path, "fail")
    assert result.returncode == NOT_RUN, result.stdout + result.stderr
    assert result.stderr.splitlines() == [
        ("invoke-elevated.sh: the elevated process did not start: "
         "This command requires an interactive window station.; Script.ps1 did not run")]
    assert result.stdout == ""


@needs_pwsh
def test_a_process_with_no_exit_code_fails_the_helper(tmp_path):
    result = run(tmp_path, "noexit")
    assert result.returncode == NOT_RUN
    assert result.stderr.splitlines() == [
        "invoke-elevated.sh: the elevated process gave no exit code; Script.ps1 did not run"]


@needs_pwsh
@pytest.mark.parametrize("code", [0, 1, 2])
def test_a_process_that_runs_gives_its_output_and_its_exit_code(tmp_path, code):
    result = run(tmp_path, f"exit {code}", ("-Check",))
    assert result.returncode == code, result.stdout + result.stderr
    assert result.stdout == f"the output of the script, exit {code}\n"
    assert result.stderr == ""
    # The log is removed.
    assert list((tmp_path / "tmp").iterdir()) == []


def task_line(task: str) -> str:
    """The command line of a task that runs the helper, as a shell line: the
    helper is a stub that exits with $STUB_RC, and the other values of the
    template are fixed words."""
    cmds = yaml.safe_load(TASKS.read_text())["tasks"][task]["cmds"]
    lines = [c for c in cmds if isinstance(c, str) and "{{.INVOKE_ELEVATED}}" in c]
    assert len(lines) == 1, task
    line = re.sub(r"\{\{if [^}]*\}\}.*?\{\{end\}\}", "", lines[0])
    line = line.replace("{{.INVOKE_ELEVATED}}", 'sh -c "exit $STUB_RC" helper')
    return re.sub(r"\{\{[^}]*\}\}", "value", line)


@pytest.mark.parametrize(("task", "passes"), [("sshd", {0}), ("sshd-check", {0, 2})])
def test_a_task_fails_when_the_script_did_not_run(tmp_path, task, passes):
    """`sshd` takes only 0, and `sshd-check` takes 2 (drift) as a pass too:
    the code of a script that did not run is neither, so both tasks fail."""
    assert f"NOT_RUN={NOT_RUN}" in HELPER.read_text()
    (tmp_path / "wslpath").write_text(WSLPATH)
    (tmp_path / "wslpath").chmod(0o755)
    line = task_line(task)
    for rc in (0, 1, 2, NOT_RUN):
        env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "STUB_RC": str(rc)}
        result = subprocess.run(["sh", "-c", line], env=env, capture_output=True, check=False)
        assert (result.returncode == 0) == (rc in passes), (task, rc)
