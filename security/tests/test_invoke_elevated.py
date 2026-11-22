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

A WSL shell can leave the Windows folders out of PATH. The helper then
takes Windows PowerShell from its standard place under the mount of the C:
drive (from `wslpath -u`), or from INVOKE_ELEVATED_POWERSHELL. The tests of
that put the stub there, with a PATH that has no Windows folder; with none,
the helper stops before any elevation.
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
# The paths of the test are the same on both sides. The mount of the C:
# drive is $STUB_C_ROOT, when the test sets it.
if [ "$1" = "-u" ] && [ "$2" = 'C:\\' ] && [ -n "$STUB_C_ROOT" ]; then
  printf '%s/\\n' "$STUB_C_ROOT"; exit 0
fi
[ "$1" = "-w" ] && shift
printf '%s\\n' "$1"
"""
# The place of Windows PowerShell under the mount of the C: drive.
STANDARD = "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

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


def stub(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


# A PATH with no Windows folder: the stubs, then the system folders only.
NO_WINDOWS = "/usr/local/bin:/usr/bin:/bin"


def run(tmp_path: Path, start: str, extra: tuple[str, ...] = (), *, on_path: bool = True,
        env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    stubs = tmp_path / "bin"
    stub(stubs / "wslpath", WSLPATH)
    if on_path:
        stub(stubs / "powershell.exe", POWERSHELL)
    (tmp_path / "prelude.ps1").write_text(PRELUDE)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH'] if on_path else NO_WINDOWS}",
           "STUB_START": start, "STUB_TMP": str(tmp_path / "tmp"),
           "STUB_PRELUDE": str(tmp_path / "prelude.ps1"), "STUB_PWSH": PWSH or "pwsh"}
    env.pop("INVOKE_ELEVATED_POWERSHELL", None)
    env.pop("STUB_C_ROOT", None)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(HELPER), str(tmp_path / "Script.ps1"), *extra],
                          capture_output=True, text=True, env=env, timeout=120, check=False)


@needs_pwsh
def test_with_no_windows_folder_on_path_the_helper_takes_the_standard_place(tmp_path):
    root = tmp_path / "c"
    stub(root / STANDARD, POWERSHELL)
    result = run(tmp_path, "exit 0", on_path=False, env_extra={"STUB_C_ROOT": str(root)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "the output of the script, exit 0\n"
    assert result.stderr == ""


@needs_pwsh
def test_the_place_of_powershell_is_settable(tmp_path):
    place = tmp_path / "elsewhere/powershell.exe"
    stub(place, POWERSHELL)
    result = run(tmp_path, "exit 2", ("-Check",), on_path=False,
                 env_extra={"INVOKE_ELEVATED_POWERSHELL": str(place)})
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == "the output of the script, exit 2\n"


@needs_pwsh
def test_powershell_on_path_comes_first(tmp_path):
    # A program at the settable place that would fail is not used.
    place = tmp_path / "elsewhere/powershell.exe"
    stub(place, "#!/bin/sh\necho wrong program >&2\nexit 99\n")
    result = run(tmp_path, "exit 0", env_extra={"INVOKE_ELEVATED_POWERSHELL": str(place)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "the output of the script, exit 0\n"
    assert result.stderr == ""


@pytest.mark.parametrize("where", ["standard", "settable"])
def test_with_no_powershell_the_helper_stops_with_one_line(tmp_path, where):
    root = tmp_path / "c"
    root.mkdir()
    if where == "standard":
        extra, missing = {"STUB_C_ROOT": str(root)}, root / STANDARD
    else:
        missing = tmp_path / "elsewhere/powershell.exe"
        extra = {"INVOKE_ELEVATED_POWERSHELL": str(missing)}
    result = run(tmp_path, "exit 0", on_path=False, env_extra=extra)
    assert result.returncode == NOT_RUN, result.stdout + result.stderr
    assert result.stderr.splitlines() == [
        f"invoke-elevated.sh: Windows PowerShell is not on PATH and not at {missing}; Script.ps1 did not run"]
    assert result.stdout == ""
    assert list((tmp_path / "tmp").iterdir()) == []


def test_with_no_mount_answer_the_helper_looks_under_mnt_c(tmp_path):
    # The stub of wslpath does not answer for the C: drive here. On a WSL
    # workstation that place holds the real program, which must not run.
    if Path("/mnt/c", STANDARD).exists():
        pytest.skip("the real Windows PowerShell is at the standard place")
    result = run(tmp_path, "exit 0", on_path=False)
    assert result.returncode == NOT_RUN
    assert result.stderr.splitlines() == [
        f"invoke-elevated.sh: Windows PowerShell is not on PATH and not at /mnt/c/{STANDARD}; Script.ps1 did not run"]


def test_the_tasks_run_no_other_windows_program_by_name():
    """The helper and the tasks that call it run Windows PowerShell only
    through the helper; each other program of their lines is a Linux one."""
    text = HELPER.read_text()
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    exes = set(re.findall(r"[\w.-]+\.exe\b", "\n".join(code)))
    # powershell.exe: the lookup on PATH, and the program that Start-Process
    # starts on the Windows side, which Windows finds by itself.
    assert exes == {"powershell.exe"}
    tasks = yaml.safe_load(TASKS.read_text())["tasks"]
    for name in ("sshd", "sshd-check"):
        lines = " ".join(c for c in tasks[name]["cmds"] if isinstance(c, str))
        assert not re.findall(r"[\w.-]+\.exe\b", lines), name


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
