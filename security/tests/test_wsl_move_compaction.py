"""The compaction of the disk file once the move of the distro is done (Design Doc 0004, section 5.10).

The disk step runs as SYSTEM, the stop of WSL is a task of the account that owns the distro, and the read of
the owner and the rules of the disk file (the check wsl-disk-rules) comes before the pause and before diskpart.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from test_wsl_start_unelevated import needs_pwsh, pwsh

REPO = Path(__file__).resolve().parents[2]
ROLES = REPO / "infrastructure/ansible/roles"
ROLE = ROLES / "windows_wsl_maintenance"
SCRIPT = ROLE / "files/Compact-WslVhdxSystem.ps1"
WINDOW = ROLES / "windows_wsl_move_rehearsal/files/Invoke-TnwksMoveWindow.ps1"
HOST_CHECK = ROLES / "windows_host_check/files/Invoke-TnwksHostCheck.ps1"


def block(text: str, begin: str, end: str) -> str:
    return text[text.index(begin):text.index(end)]


def test_the_read_of_the_compute_systems_is_the_windows_word_for_word():
    text = SCRIPT.read_text()
    copy = block(text, "$TnwksHcsSource = @'", "# --- END compute systems")
    window = WINDOW.read_text()
    assert copy.strip() in window
    assert "function Get-TnwksComputeSystem {" in copy


def test_the_script_reads_the_host_checks_own_read_only_after_its_hash():
    text = SCRIPT.read_text()
    run = block(text, "function Invoke-TnwksCompaction {", "if (-not $env:TNWKS_COMPACT_LIBRARY)")
    assert run.index("Get-FileHash -LiteralPath $HostCheck") < run.index(". $HostCheck -Library")
    assert "function Measure-TnwksWslDisk {" in HOST_CHECK.read_text()
    # The read before the pause, the read before diskpart, and diskpart last.
    first, second = run.index("Test-TnwksDiskRead 'before the pause'"), run.index("Test-TnwksDiskRead 'before diskpart'")
    assert first < run.index("Disable-ScheduledTask") < run.index("Start-ScheduledTask -TaskName $StopTask") < second
    assert second < run.index("'compact vdisk'")
    # No privilege, no owner and no rules.
    for word in ("AdjustTokenPrivileges", "SeBackupPrivilege", "Set-Acl", "SetAccessControl", "SetOwner", "icacls"):
        assert word not in text, word


FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
$global:Reads = New-Object System.Collections.Generic.Queue[object]
$global:Enabled = @{ 'tnwks-a-account' = $true; 'tnwks-b-account' = $true; 'tnwks-c-account' = $false }
function Get-ScheduledTask([string]$TaskName, $ErrorAction) {
    if ($TaskName -eq 'tnwks-wsl-stop-account') { return [pscustomobject]@{ State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } } }
    if (-not $global:Enabled.ContainsKey($TaskName)) { return $null }
    [pscustomobject]@{ State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $global:Enabled[$TaskName] } }
}
function Get-ScheduledTaskInfo([string]$TaskName) { [pscustomobject]@{ LastTaskResult = 0 } }
function Disable-ScheduledTask([string]$TaskName) { $global:Calls.Add("disable $TaskName"); $global:Enabled[$TaskName] = $false }
function Enable-ScheduledTask([string]$TaskName) { $global:Calls.Add("enable $TaskName"); $global:Enabled[$TaskName] = $true }
function Stop-ScheduledTask([string]$TaskName) { $global:Calls.Add("stop $TaskName") }
function Start-ScheduledTask([string]$TaskName) { $global:Calls.Add("start $TaskName") }
function diskpart.exe { $global:Calls.Add('diskpart ' + (Get-Content -LiteralPath $args[1] | Select-Object -Index 2)); $global:LASTEXITCODE = 0 }
function Start-Sleep { }
"""

HOST_CHECK_FAKE = r"""param([switch]$Library)
function Measure-TnwksWslDisk([string]$Path, [string]$Account, [string]$CapabilitySid) {
    $global:Calls.Add("read $Path $Account")
    $global:Reads.Dequeue()
}
"""


def run(tmp_path: Path, reads: list[list[str]], sha: str | None = None) -> tuple[str, list[str]]:
    check = tmp_path / "Invoke-TnwksHostCheck.ps1"
    check.write_text(HOST_CHECK_FAKE)
    digest = sha or hashlib.sha256(check.read_bytes()).hexdigest().upper()
    reads_ps = "; ".join(f"$global:Reads.Enqueue(@({', '.join(repr(r) for r in read)}))" for read in reads)
    body = f"""{FAKES}
{reads_ps}
$env:TNWKS_COMPACT_LIBRARY = '1'
. '{SCRIPT}' -Vhdx 'C:\\x\\ext4.vhdx' -Account standin-wsl -CapabilitySid S-1-15-3-1 -HostCheck '{check}' `
  -HostCheckSha256 '{digest}' -StopTask tnwks-wsl-stop-account -Paused 'tnwks-a-account,tnwks-b-account,tnwks-c-account' `
  -LogDir '{tmp_path}'
function Get-TnwksComputeSystem {{ $global:Calls.Add('hcs'); @() }}
$outcome = Invoke-TnwksCompaction
@{{ outcome = "$outcome"; calls = @($global:Calls) }} | ConvertTo-Json -Compress
"""
    result = pwsh(body)
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout.strip().splitlines()[-1])
    return doc["outcome"], doc["calls"]


@needs_pwsh
def test_a_pass_pauses_stops_reads_again_compacts_and_starts_the_tasks_again(tmp_path):
    outcome, calls = run(tmp_path, [[], []])
    assert outcome == "done"
    assert calls == [
        "read C:\\x\\ext4.vhdx standin-wsl",
        "disable tnwks-a-account", "stop tnwks-a-account", "disable tnwks-b-account", "stop tnwks-b-account",
        "start tnwks-wsl-stop-account", "hcs",
        "read C:\\x\\ext4.vhdx standin-wsl",
        "diskpart compact vdisk",
        "enable tnwks-a-account", "enable tnwks-b-account", "start tnwks-a-account", "start tnwks-b-account",
    ]


@needs_pwsh
def test_a_fail_of_the_first_read_pauses_nothing(tmp_path):
    outcome, calls = run(tmp_path, [["owner"]])
    assert outcome == "stopped" and calls == ["read C:\\x\\ext4.vhdx standin-wsl"]
    assert "failed: owner" in (tmp_path / "compact-system.log").read_text()


@needs_pwsh
def test_a_fail_of_the_second_read_runs_no_diskpart_and_starts_the_tasks_again(tmp_path):
    outcome, calls = run(tmp_path, [[], ["entry"]])
    assert outcome == "stopped"
    assert not any(call.startswith("diskpart") for call in calls)
    assert calls[-4:] == ["enable tnwks-a-account", "enable tnwks-b-account", "start tnwks-a-account", "start tnwks-b-account"]


@needs_pwsh
def test_another_host_check_script_changes_nothing(tmp_path):
    outcome, calls = run(tmp_path, [[]], sha="0" * 64)
    assert outcome == "stopped" and calls == []


def test_the_role_registers_the_compaction_of_system_and_the_stop_task_of_the_account():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    named = {task.get("name"): task for task in tasks}
    stop = named["Stop task of the account that owns the distro"]
    assert stop["when"] == "wsl_move_phase != 'off'"
    assert stop["tnwks_win_scheduled_task"]["s4u_user"] == "{{ wsl_move_new_account }}"
    assert stop["vars"]["ansible_become_user"] == "SYSTEM"
    compact = named["vhdx compaction task of SYSTEM, once the move is done"]
    assert compact["when"] == "wsl_move_phase == 'done'"
    assert "s4u_user" not in compact["tnwks_win_scheduled_task"]
    xml = (ROLE / "templates/vhdx-compact-system.xml.j2").read_text()
    assert "<UserId>S-1-5-18</UserId>" in xml and "Compact-WslVhdxSystem.ps1" in xml
    stop_xml = (ROLE / "templates/wsl-stop.xml.j2").read_text()
    assert "--shutdown" in stop_xml and "<RunLevel>LeastPrivilege</RunLevel>" in stop_xml


def test_the_compaction_takes_the_values_of_the_host_check_and_pauses_the_start_tasks_of_the_account():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    host_check = yaml.safe_load((ROLES / "windows_host_check/defaults/main.yml").read_text())
    assert defaults["vhdx_compact_capability_sid"] == host_check["host_check_wsl_capability_sid"]
    assert defaults["vhdx_compact_host_check"] == host_check["host_check_dir"] + "\\" + host_check["host_check_script"]
    assert "windows_host_check/files/Invoke-TnwksHostCheck.ps1', rstrip=false" in defaults["vhdx_compact_host_check_sha256"]
    assert defaults["vhdx_compact_paused"] == "{{ wsl_move_new_start_tasks }}"
