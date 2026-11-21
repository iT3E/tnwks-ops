"""The monitoring data of site 1 in a file on the disk of the distro (Design Doc 0004,
section 5.13).

The file is formatted once, with the UUID it records first, and is mounted by a loop
device at the point of the VHDX before it. It is never mounted at two places, over
another file system or over data. The Windows mount task is disabled with the file, and
it and the VHDX stay as the way back. These tests run the guards of the script with
fakes for the commands, and read the roles and the playbooks.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import ansible_render
import pytest
import yaml
from test_wsl_start_unelevated import FAKES, needs_pwsh, pwsh

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "infrastructure/ansible"
ROLE = ANSIBLE / "roles/wsl_observability_file"
WINDOWS_ROLE = ANSIBLE / "roles/windows_observability_storage"
UUID = "1e3df741-9195-4b1b-b612-284a74e6e425"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load_script("observability_file", ROLE / "files/observability-file.py")
volume = load_script("observability_volume", WINDOWS_ROLE / "files/observability-volume.py")


@pytest.fixture
def place(tmp_path, monkeypatch):
    """The folder of the file in tmp_path, with no command run."""
    folder = tmp_path / "lib"
    monkeypatch.setattr(script, "DIR", folder)
    monkeypatch.setattr(script, "IMAGE", folder / "observability.ext4")
    monkeypatch.setattr(script, "STATE", folder / "file.json")
    monkeypatch.setattr(script, "STAGE", folder / "stage")
    return folder


def record(phase: str = "initialized") -> dict:
    return {"path": str(script.IMAGE), "size_gib": 19, "uuid": UUID, "phase": phase}


def sized(path: Path) -> None:
    with path.open("wb") as out:
        out.truncate(19 * script.GIB)


# --- The file and its file system ------------------------------------------------------------

def test_a_file_with_no_record_is_never_used_or_formatted(place):
    place.mkdir()
    script.IMAGE.write_bytes(b"")
    with patch.object(script, "run") as run, pytest.raises(AssertionError, match="no record"):
        script.create()
    run.assert_not_called()
    assert not script.STATE.exists()


def test_the_uuid_is_recorded_before_the_file_is_made_and_the_format_takes_it(place):
    calls = []

    def run(*args):
        calls.append(args)
        if args[0] == "fallocate":
            assert json.loads(script.STATE.read_text())["phase"] == "pending", "the record comes first"
            sized(script.IMAGE)
        if args[0] == "losetup":
            return ""
        if args[0] == "wipefs":
            return '{"signatures": []}'
        return ""

    found = iter([{}, None])

    def probe():
        value = next(found)
        return value if value is not None else {"TYPE": "ext4", "UUID": json.loads(script.STATE.read_text())["uuid"]}

    with patch.object(script, "run", side_effect=run), patch.object(script, "probe", side_effect=probe):
        assert script.create() is True
    saved = json.loads(script.STATE.read_text())
    assert saved["phase"] == "initialized" and saved["size_gib"] == 19
    assert [call[0] for call in calls] == ["fallocate", "losetup", "wipefs", "mkfs.ext4"]
    assert calls[0][1:] == ("--length", str(19 * script.GIB), str(script.IMAGE))
    mkfs = calls[-1]
    assert mkfs[mkfs.index("-U") + 1] == saved["uuid"] and mkfs[-1] == str(script.IMAGE)
    assert "-F" not in mkfs, "mkfs asks before it formats over a file system"
    assert oct(place.stat().st_mode & 0o777) == "0o700"


@pytest.mark.parametrize("found", [{"TYPE": "ext4", "UUID": "another"}, {"TYPE": "xfs", "UUID": UUID},
                                   {"PTTYPE": "gpt"}])
def test_a_file_with_another_signature_is_never_formatted(place, found):
    place.mkdir()
    script.write_state(record("pending"))
    sized(script.IMAGE)
    with patch.object(script, "run", return_value="") as run, patch.object(script, "probe", return_value=found), \
            pytest.raises(AssertionError):
        script.create()
    assert all(call.args[0] != "mkfs.ext4" for call in run.call_args_list)


def test_a_wipefs_signature_blocks_the_format(place):
    place.mkdir()
    script.write_state(record("pending"))
    sized(script.IMAGE)

    def run(*args):
        return '{"signatures": [{"type": "dos"}]}' if args[0] == "wipefs" else ""

    with patch.object(script, "run", side_effect=run) as fake, patch.object(script, "probe", return_value={}), \
            pytest.raises(AssertionError, match="Signatures"):
        script.create()
    assert all(call.args[0] != "mkfs.ext4" for call in fake.call_args_list)


def test_an_attached_file_is_never_formatted(place):
    place.mkdir()
    script.write_state(record("pending"))
    sized(script.IMAGE)
    with patch.object(script, "run", return_value="") as run, patch.object(script, "loops", return_value=["/dev/loop0"]), \
            pytest.raises(AssertionError, match="attached"):
        script.create()
    assert all(call.args[0] != "mkfs.ext4" for call in run.call_args_list)


def test_a_made_file_is_only_checked(place):
    place.mkdir()
    script.write_state(record())
    sized(script.IMAGE)
    with patch.object(script, "run") as run, \
            patch.object(script, "probe", return_value={"TYPE": "ext4", "UUID": UUID}):
        assert script.create() is False
    run.assert_not_called()


def test_a_record_of_another_file_is_refused(place):
    place.mkdir()
    script.write_state({**record(), "size_gib": 20})
    with pytest.raises(AssertionError, match="another file"):
        script.read_state()


def test_the_loop_devices_of_the_file_are_read_by_their_back_file(place):
    listing = {"loopdevices": [{"name": "/dev/loop0", "back-file": str(script.IMAGE)},
                               {"name": "/dev/loop1", "back-file": str(script.IMAGE) + " (deleted)"},
                               {"name": "/dev/loop2", "back-file": "/other"}]}
    with patch.object(script, "run", return_value=json.dumps(listing)):
        assert script.loops() == ["/dev/loop0"]
    with patch.object(script, "run", return_value=""):
        assert script.loops() == []


# --- The mount -------------------------------------------------------------------------------

def test_the_point_is_the_point_of_the_vhdx():
    assert (script.WORKER, script.CLUSTER, script.STORAGE) == (volume.WORKER, volume.CLUSTER, volume.STORAGE)
    assert script.MARKER == ".tnwks-observability-id", "the provisioner and the storage check read it"
    mounts = [{"Type": "volume", "Destination": "/var", "Source": "/docker/var/_data"}]
    labels = {"talos.cluster.name": script.CLUSTER, "talos.type": "worker"}
    with patch.object(script, "run", return_value=json.dumps(labels) + "\n" + json.dumps(mounts) + "\n") as run:
        assert script.mountpoint() == Path("/docker/var/_data") / script.STORAGE
    assert "--format" in run.call_args.args, "plain inspect output holds the Talos config"


def test_the_bare_point_is_immutable_before_the_loop_mount_covers_it(place):
    point = place / "point"
    with patch.object(script, "mounted", side_effect=[False, True]), patch.object(script, "verify"), \
            patch.object(script, "loops", return_value=[]), patch.object(script, "run") as run:
        script.attach(point, record())
    calls = [call.args for call in run.call_args_list]
    assert [call[0] for call in calls] == ["chattr", "mount"]
    assert calls[1] == ("mount", "-t", "ext4", "-o", "loop,noatime", str(script.IMAGE), str(point))
    assert (point / script.MARKER).read_text() == UUID + "\n"


def test_the_stage_takes_no_immutable_flag(place):
    place.mkdir()
    with patch.object(script, "mounted", side_effect=[False, True]), patch.object(script, "verify"), \
            patch.object(script, "loops", return_value=[]), patch.object(script, "run") as run:
        script.attach(script.STAGE, record())
    assert [call.args[0] for call in run.call_args_list] == ["mount"]


def test_another_file_system_at_the_point_is_never_covered(place):
    """With the VHDX still at the point, the timer fails and changes nothing."""
    point = place / "point"
    point.mkdir(parents=True)
    with patch.object(script, "mounted", return_value=False), patch.object(script, "verify"), \
            patch.object(Path, "is_mount", return_value=True), patch.object(script, "run") as run, \
            pytest.raises(AssertionError, match="another file system"):
        script.attach(point, record())
    run.assert_not_called()


def test_data_at_a_bare_point_is_never_covered(place):
    point = place / "point"
    point.mkdir(parents=True)
    (point / "stray").write_text("data")
    with patch.object(script, "mounted", return_value=False), patch.object(script, "verify"), \
            patch.object(script, "loops", return_value=[]), patch.object(script, "run") as run, \
            pytest.raises(AssertionError, match="holds data"):
        script.attach(point, record())
    run.assert_not_called()


def test_the_file_is_never_mounted_at_two_places(place):
    """Mounted at the stage for the copy, it is not mounted at the point."""
    point = place / "point"
    with patch.object(script, "mounted", return_value=False), patch.object(script, "verify"), \
            patch.object(script, "loops", return_value=["/dev/loop0"]), patch.object(script, "run") as run, \
            pytest.raises(AssertionError, match="another place"):
        script.attach(point, record())
    run.assert_not_called()


def test_a_file_that_is_not_made_is_never_mounted(place):
    with patch.object(script, "run") as run, pytest.raises(AssertionError, match="not made"):
        script.attach(place / "point", record("pending"))
    run.assert_not_called()


def test_a_marker_of_another_file_system_stops_the_mount(place):
    point = place / "point"
    point.mkdir(parents=True)
    (point / script.MARKER).write_text("another\n")
    with patch.object(script, "mounted", return_value=True), patch.object(script, "run"), \
            pytest.raises(AssertionError, match="marker"):
        script.attach(point, record())


def test_the_release_unmounts_only_the_file(place):
    point = place / "point"
    with patch.object(script, "mounted", side_effect=[True, False]), patch.object(script, "run") as run:
        script.release(point)
    assert [call.args for call in run.call_args_list] == [("umount", str(point))]
    with patch.object(script, "mounted", return_value=False), patch.object(script, "run") as run:
        script.release(point)
    run.assert_not_called()


# --- The roles and the playbooks -------------------------------------------------------------

def test_the_distro_play_runs_the_role_only_where_the_windows_host_has_the_volume():
    playbook = yaml.safe_load((ANSIBLE / "playbooks/wsl-distro.yml").read_text())
    entry = next(role for role in playbook[0]["roles"] if role["role"] == "wsl_observability_file")
    assert entry["when"] == "hostvars[wsl_distro_windows_host].observability_storage_enabled | default(false) | bool"
    assert entry["tags"] == ["observability-file"]


def test_the_timer_mounts_the_file_only_in_the_phase_file():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    timer = [task for task in tasks if "ansible.builtin.systemd" in task]
    assert len(timer) == 1 and timer[0]["ansible.builtin.systemd"] == {
        "name": "{{ wsl_observability_file_unit }}.timer", "state": "stopped", "enabled": False}
    assert "wsl_observability_file_mount == 'vhdx'" in timer[0]["when"]
    block = next(task for task in tasks if "block" in task)
    assert block["when"] == "wsl_observability_file_mount != 'vhdx'"
    started = [task for task in block["block"] if task.get("ansible.builtin.systemd", {}).get("state") == "started"]
    assert len(started) == 1 and "wsl_observability_file_mount == 'file'" in started[0]["when"]
    service = (ROLE / "files/tnwks-observability-file.service").read_text()
    assert "observability-file.py mount" in service and "Type=oneshot" in service
    timer_unit = (ROLE / "files/tnwks-observability-file.timer").read_text()
    assert "OnUnitActiveSec=1min" in timer_unit and "WantedBy=timers.target" in timer_unit
    assert (ROLE / "files/observability-file.py").read_text().startswith("#!/usr/bin/python3\n")


def test_with_the_file_nothing_attaches_the_vhdx_and_it_stays():
    tasks = yaml.safe_load((WINDOWS_ROLE / "tasks/main.yml").read_text())
    attach = next(task for task in tasks if "win_powershell" in str(task) and "-Initialize" in str(task))
    assert attach["when"] == "observability_storage_mount != 'file'"
    register = next(task for task in tasks if "tnwks_win_scheduled_task" in task)
    assert register["when"] == "observability_storage_mount != 'file'", "with the file, never registered again"
    assert not any(task.get("tnwks_win_scheduled_task", {}).get("state") == "absent" for task in tasks)
    assert not any("win_file" in task and task["ansible.windows.win_file"].get("state") == "absent" for task in tasks)
    defaults = yaml.safe_load((WINDOWS_ROLE / "defaults/main.yml").read_text())
    assert defaults["observability_storage_mount"] == "vhdx"


@ansible_render.no_ansible
def test_the_distro_reads_the_phase_of_its_windows_host(tmp_path):
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    phase = defaults["wsl_observability_file_mount"].strip()
    assert phase.startswith("{{ ") and phase.endswith(" }}")
    hosts = {}
    for mount in ("vhdx", "ready", "file", None):
        hosts[f"win-{mount}"] = {"observability_storage_mount": mount} if mount else {}
        hosts[f"distro-{mount}"] = {"wsl_distro_windows_host": f"win-{mount}"}
    distros = [name for name in hosts if name.startswith("distro-")]
    out = ansible_render.render(tmp_path, hosts, distros, {"phase": phase[3:-3]})
    for mount in ("vhdx", "ready", "file"):
        assert out[f"distro-{mount}"]["phase"] == mount
    assert out["distro-None"]["phase"] == "vhdx"


def disable_script() -> str:
    tasks = yaml.safe_load((WINDOWS_ROLE / "tasks/main.yml").read_text())
    task = next(task for task in tasks if task.get("when") == "observability_storage_mount == 'file'")
    return task["ansible.windows.win_powershell"]["script"]


TASK_FAKES = r"""
$global:Calls = New-Object System.Collections.Generic.List[string]
function Get-ScheduledTask { param([string]$TaskPath, [string]$TaskName)
    if (-not $global:Task) { return $null }
    $global:Task }
function Disable-ScheduledTask { param($InputObject) $global:Calls.Add('disable'); $global:Task.Settings.Enabled = $false }
function Stop-ScheduledTask { param([string]$TaskPath, [string]$TaskName) $global:Calls.Add('stop') }
function Register-ScheduledTask { $global:Calls.Add('register') }
"""


@needs_pwsh
@pytest.mark.parametrize(("enabled", "state", "check", "calls", "changed"), [
    (True, "Ready", False, ["disable", "stop"], True),
    (False, "Running", False, ["disable", "stop"], True),
    (False, "Disabled", False, [], False),
    (True, "Ready", True, [], True),
])
def test_with_the_file_the_mount_task_is_disabled_in_place(enabled, state, check, calls, changed):
    """Never registered again: a principal of a standard account with no password is refused."""
    task = (f"$global:Task = [pscustomobject]@{{ State = '{state}'; "
            f"Settings = [pscustomobject]@{{ Enabled = ${str(enabled).lower()} }} }}\n")
    result = pwsh(TASK_FAKES + task + f"$Ansible = [pscustomobject]@{{ Changed = $null; CheckMode = ${str(check).lower()} }}\n"
                  + "& {\n" + disable_script() + "\n} -Name tnwks-observability-mount\n"
                  + "[Console]::Out.Write((@{ calls = @($global:Calls); changed = $Ansible.Changed } | ConvertTo-Json -Compress))")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["calls"] == calls and out["changed"] is changed


@needs_pwsh
def test_with_the_file_a_missing_mount_task_stops_the_play():
    result = pwsh(TASK_FAKES + "$global:Task = $null\n$Ansible = [pscustomobject]@{ Changed = $null; CheckMode = $false }\n"
                  + "$ErrorActionPreference = 'Stop'\n& {\n" + disable_script() + "\n} -Name tnwks-observability-mount\n")
    assert result.returncode != 0 and "way back" in result.stderr + result.stdout


@needs_pwsh
def test_a_compaction_leaves_a_disabled_mount_task_disabled_and_stopped(tmp_path):
    compact = ANSIBLE / "roles/windows_wsl_maintenance/files/Compact-WslVhdx.ps1"
    vhdx = tmp_path / "ext4.vhdx"
    vhdx.write_bytes(b"x")
    result = pwsh(FAKES + r"""
function Get-ScheduledTask { param([string]$TaskName)
    [pscustomobject]@{ State = $(if ($TaskName -eq 'tnwks-observability-mount') { 'Disabled' } else { 'Ready' }) } }
function Enable-ScheduledTask { param([string]$TaskName) $global:Calls.Add("enable $TaskName") }
function Split-Path { 'E:' }
function Get-PSDrive { [pscustomobject]@{ Free = 1GB } }
& '""" + str(compact) + f"' -Distro Ubuntu -Vhdx '{vhdx}' -LogDir '{tmp_path}'"
                    + "\n[Console]::Out.Write(($global:Calls -join \"`n\"))\n")
    calls = result.stdout.splitlines()
    assert "diskpart" in calls, result.stdout + result.stderr
    assert "enable tnwks-observability-mount" not in calls and "start tnwks-observability-mount" not in calls
    for task in ("tnwks-wsl-keepalive", "tnwks-usb-attach", "tnwks-lan-bridge-sync"):
        assert f"enable {task}" in calls and f"start {task}" in calls, task
