"""The scripts of the rehearsal of the move, run as the play runs them, and the play's checks fed with what they give.

Each embedded script of the role's task files runs here in PowerShell, as win_powershell
runs it: with its parameters, a $Ansible object, and stand-ins for the parts of Windows
(scheduled tasks, processes, accounts). Its output goes through a model of what
win_powershell keeps of it (two levels by default: a list or an object deeper than that
becomes text), and then into the asserts and failed_when checks of the phase, which run in
ansible-playbook on this host. So a check that reads a value the script never gives in
that shape (an empty file, a log of one line, a key that is not there, a list that comes
back as text) fails here, not on the host.

What stays for the host: Windows PowerShell 5.1 itself (here it is PowerShell 7), the
answers of Windows behind the stand-ins, and WSL (docs/wsl/windows-ansible.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from test_wsl_move_rehearsal import (
    A_SID,
    ANSIBLE,
    B_SID,
    DEFAULTS,
    OLD_SDDL,
    ROLE,
    WINDOW,
    WINDOW_FAKES,
    run_window,
)
from test_wsl_start_unelevated import needs_pwsh, pwsh

UV = shutil.which("uv")
no_ansible = pytest.mark.skipif(UV is None and not os.environ.get("CI"),
                                reason="no uv on this workstation; CI runs these tests")
RUNNER = ROLE / "files/Invoke-TnwksStartTask.ps1"
LIVE = ROLE / "files/Get-TnwksMoveLiveState.ps1"
# The stops task on this file system: the separators of the path of powershell.exe, and a
# service binary with a version (an assembly of PowerShell itself).
SLASH_EDIT = (r'"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"',
              '"$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe"')
PSHOME_EDIT = ("$cfg = $TestConfig | ConvertFrom-Json",
               "$TestConfig = $TestConfig.Replace('__PSHOME__', $PSHOME); $cfg = $TestConfig | ConvertFrom-Json")


def needs_both(test):
    return needs_pwsh(no_ansible(test))


# --- The task files -----------------------------------------------------------------------

def walk(tasks: list | None):
    for task in tasks or []:
        if "block" in task:
            yield from walk(task["block"])
            yield from walk(task.get("rescue"))
            yield from walk(task.get("always"))
        else:
            yield task


def tasks_in(file: str) -> list[dict]:
    return list(walk(yaml.safe_load((ROLE / "tasks" / file).read_text())))


def find_task(file: str, name: str) -> dict:
    found = [t for t in tasks_in(file) if t.get("name", "").startswith(name)]
    assert found, f"{file}: no task {name}"
    return found[0]


def checks_of(file: str, names: tuple[str, ...] = ()) -> list[dict]:
    """The asserts, the set_fact tasks and the failed_when checks of a task file, in order.

    A failed_when becomes an assert of its negation, with the task's own vars. The
    become vars and each when go: the play runs here, not in check mode.
    """
    out = []
    for task in tasks_in(file):
        name = task.get("name", "")
        if names and not name.startswith(names):
            continue
        task_vars = {k: v for k, v in (task.get("vars") or {}).items() if not k.startswith("ansible_")}
        if "ansible.builtin.assert" in task or "ansible.builtin.set_fact" in task:
            kept = {k: v for k, v in task.items() if k not in ("when", "vars")}
            if task_vars:
                kept["vars"] = task_vars
            out.append(kept)
        elif "failed_when" in task:
            fw = task["failed_when"]
            fw = " or ".join(f"({c})" for c in fw) if isinstance(fw, list) else fw
            check = {"name": f"failed_when of {name}", "ansible.builtin.assert": {"that": [f"not ({fw})"], "quiet": True}}
            if task_vars:
                check["vars"] = task_vars
            out.append(check)
    return out


# --- The scripts, in PowerShell -----------------------------------------------------------

# What win_powershell keeps of the output (its option depth, 2 by default): the list of
# output objects at that depth, each object one level below, and so on, as the module's
# Convert-OutputObject (ansible.windows 3.9.2) reads each value:
#   - an enum, at level 0 or above, becomes {Type, String, Value}; below, its number;
#   - a date becomes its text in the round-trip form ('o');
#   - a string, a switch (its flag) and any other value type stay as they are;
#   - below level 0, anything else becomes its text: a list is joined by spaces, an
#     object reads "@{name=value; ...}";
#   - a list (IList) becomes a list, a dictionary a dictionary (its note properties,
#     then its keys), and anything else, also an enumerable that is not a list, the
#     dictionary of its properties.
# Written from the module's behaviour, and checked against its function.
DEPTH_MODEL = r"""
function ConvertTo-TnwksModuleOutput($Value, [int]$Depth) {
    if ($null -eq $Value) { return , $null }
    if ($Value -is [Enum] -and $Depth -ge 0) {
        return , @{ Type = $Value.GetType().FullName; String = $Value.ToString(); Value = [int]$Value }
    }
    if ($Value -is [DateTime] -or $Value -is [DateTimeOffset]) { return , $Value.ToString('o') }
    if ($Value -is [string]) { return , $Value }
    if ($Value -is [switch]) { return , $Value.IsPresent }
    if ($Value.GetType().IsValueType) { return , $Value }
    if ($Depth -lt 0) { return , [string]$Value }
    if ($Value -is [Collections.IList]) {
        $l = [Collections.Generic.List[object]]::new()
        foreach ($i in $Value) { $l.Add((ConvertTo-TnwksModuleOutput $i ($Depth - 1))) }
        return , $l
    }
    $h = @{}
    if ($Value -is [Collections.IDictionary]) {
        foreach ($p in $Value.PSObject.Properties) {
            if ($p.MemberType -in @('AliasProperty', 'ScriptProperty', 'NoteProperty')) {
                $h[$p.Name] = ConvertTo-TnwksModuleOutput $p.Value ($Depth - 1) }
        }
        foreach ($k in $Value.Keys) { $h["$k"] = ConvertTo-TnwksModuleOutput $Value[$k] ($Depth - 1) }
        return , $h
    }
    foreach ($p in $Value.PSObject.Properties) { $h[$p.Name] = ConvertTo-TnwksModuleOutput $p.Value ($Depth - 1) }
    return , $h
}
"""


def ps_str(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_script(tmp_path: Path, file: str, name: str, params: dict, stand_ins: str = "", depth: int | None = None,
               edits: tuple[tuple[str, str], ...] = (), fails: bool = False, changed: bool = False):
    """Runs the script of a win_powershell task as the module does, and gives its output.

    edits: texts of the script to replace, for a path that only Windows has.
    fails: the script must throw; the result is then its error text.
    changed: give the output and the value of $Ansible.Changed, as a pair.
    """
    module = find_task(file, name)["ansible.windows.win_powershell"]
    script = module["script"].replace("{{ wsl_move_rehearsal_procedure_lib }}", VARS["wsl_move_rehearsal_procedure_lib"])
    for old, new in edits:
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    depth = int(module.get("depth", 2)) if depth is None else depth
    tag = uuid.uuid4().hex[:8]
    script_file = tmp_path / f"script-{tag}.ps1"
    params_file = tmp_path / f"params-{tag}.json"
    script_file.write_text(script)
    params_file.write_text(json.dumps(params))
    run = pwsh(DEPTH_MODEL + stand_ins + f"""
$Ansible = [pscustomobject]@{{ Changed = $false }}
$params = @{{}}
foreach ($p in (Get-Content -LiteralPath {ps_str(params_file)} -Raw | ConvertFrom-Json -NoEnumerate).PSObject.Properties) {{
    $params[$p.Name] = $p.Value }}
$sb = [scriptblock]::Create((Get-Content -LiteralPath {ps_str(script_file)} -Raw))
$out = @(& $sb @params)
$list = [Collections.Generic.List[object]]::new()
foreach ($item in $out) {{ $list.Add((ConvertTo-TnwksModuleOutput $item {depth - 1})) }}
[Console]::Out.Write((ConvertTo-Json -InputObject @{{ out = $list; changed = [bool]$Ansible.Changed }} -Depth 50 -Compress))
""")
    if fails:
        assert run.returncode != 0, run.stdout
        return run.stderr
    assert run.returncode == 0, run.stderr + run.stdout
    # An error record does not fail win_powershell (error_action continue), but it is a
    # fault of the script all the same.
    assert run.stderr.strip() == "", run.stderr
    result = json.loads(run.stdout)
    return (result["out"], result["changed"]) if changed else result["out"]


@needs_pwsh
def test_the_model_gives_an_enum_as_a_dictionary_and_a_date_as_text():
    # A script that gives the state of a task or a time as it is: the play reads
    # neither as the text that it compares.
    run = pwsh(DEPTH_MODEL + TASK_STATE + r"""
$set = [Collections.Generic.HashSet[string]]::new(); [void]$set.Add('a')
$item = [pscustomobject]@{ state = [TnwksTaskState]'Running'; text = "$([TnwksTaskState]'Running')"
    time = [datetime]::new(2026, 1, 2, 3, 4, 5, [DateTimeKind]::Utc); set = $set; deep = [pscustomobject]@{ state = [TnwksTaskState]'Ready' } }
[Console]::Out.Write((ConvertTo-Json -InputObject (ConvertTo-TnwksModuleOutput $item 1) -Depth 9 -Compress))
""")
    out = json.loads(run.stdout)
    assert out["state"] == {"Type": "TnwksTaskState", "String": "Running", "Value": 4} and out["text"] == "Running"
    # An enumerable that is not a list gives its properties, not its items.
    assert out["time"] == "2026-01-02T03:04:05.0000000Z" and out["set"]["Count"] == 1 and "a" not in out["set"]
    assert out["deep"] == {"state": 3}


def registered(output: list) -> dict:
    return {"changed": False, "failed": False, "output": output}


# --- The play's checks, in ansible-playbook -----------------------------------------------

def run_plays(tmp_path: Path, plays: list[dict]) -> list[dict]:
    """Runs each play on this host: its tasks (checks_of), with the role's defaults and vars
    and its own vars. Each play gives, per task, whether it failed and why, and the facts
    named in its keep."""
    root = tmp_path / f"plays-{uuid.uuid4().hex[:8]}"
    root.mkdir()
    (root / "ansible.cfg").write_text("[defaults]\nlocalhost_warning = false\nretry_files_enabled = false\n")
    book = []
    for i, play in enumerate(plays):
        (root / f"vars-{i}.json").write_text(json.dumps({**FILE_VARS, **play.get("vars", {})}))
        tasks = []
        for j, task in enumerate(play["tasks"]):
            tasks.append({**task, "ignore_errors": True, "register": f"tnwks_check_{j}"})
        results = ", ".join(
            f"{{'name': {json.dumps(t.get('name', ''))}, 'failed': tnwks_check_{j}.failed | default(false), "
            f"'assertion': tnwks_check_{j}.assertion | default(''), 'msg': tnwks_check_{j}.msg | default('')}}"
            for j, t in enumerate(play["tasks"]))
        keep = ", ".join(f"{json.dumps(k)}: {k} | default(none)" for k in play.get("keep", []))
        tasks.append({"name": "Write the results", "ansible.builtin.copy": {
            "dest": str(root / f"out-{i}.json"), "mode": "0600",
            "content": f"{{{{ {{'results': [{results}], 'facts': {{{keep}}}}} | to_json }}}}"}})
        book.append({"name": f"play {i}", "hosts": "localhost", "connection": "local", "gather_facts": False,
                     "vars": {"ansible_python_interpreter": "{{ ansible_playbook_python }}"},
                     "vars_files": [str(ROLE / "defaults/main.yml"), str(ROLE / "vars/main.yml"), str(root / f"vars-{i}.json")],
                     "tasks": tasks})
    (root / "play.yml").write_text(yaml.safe_dump(book, sort_keys=False))
    env = {**os.environ, "ANSIBLE_CONFIG": str(root / "ansible.cfg"), "ANSIBLE_HOME": str(root / "home"),
           "ANSIBLE_NOCOLOR": "1"}
    run = subprocess.run([UV or "uv", "run", "-q", "--project", str(ANSIBLE), "--locked", "ansible-playbook",
                          "-i", "localhost,", str(root / "play.yml")],
                         check=False, capture_output=True, text=True, timeout=600, env=env)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]
    return [json.loads((root / f"out-{i}.json").read_text()) for i in range(len(plays))]


def passed(result: dict) -> list[str]:
    """The tasks of a play that did not pass, each with its message."""
    return [f"{r['name']}: {r['msg']}" for r in result["results"] if r["failed"]]


def failed_by_assert(result: dict, name: str) -> bool:
    """The check failed as an assert (its condition was false), not on an error of a template."""
    r = next(r for r in result["results"] if r["name"].startswith(name))
    return r["failed"] and bool(r["assertion"])


# --- Stand-ins for Windows ----------------------------------------------------------------

# Task Scheduler: each start is a new run, with the exit code of $global:Code. With
# $global:QueuedPolls, a run first reads Queued with the result 0x41301 (the task is
# running) for that many reads, with its new run time already, as a task that never ran
# can; $global:PendingNow is the count left. As on Windows, the state of a task is an
# enum and a time is a date: a script that gives one as it is gets {Type, String, Value}
# or a text from win_powershell.
TASK_STATE = r"""
enum TnwksTaskState { Unknown = 0; Disabled = 1; Queued = 2; Ready = 3; Running = 4 }
"""
TASKS = TASK_STATE + r"""
$global:Last = [datetime]'2026-01-01'
$global:Code = 0
$global:Started = @()
$global:PendingNow = 0
function Get-ScheduledTaskInfo {
    param([string]$TaskName)
    if ($global:PendingNow -gt 0) {
        $global:PendingNow--
        return [pscustomobject]@{ LastRunTime = $global:Last; LastTaskResult = 0x41301 }
    }
    [pscustomobject]@{ LastRunTime = $global:Last; LastTaskResult = $global:Code }
}
function Start-ScheduledTask {
    param([string]$TaskName)
    $global:Started += $TaskName
    $global:Last = $global:Last.AddMinutes(1)
    $global:PendingNow = [int]$global:QueuedPolls
    if ($global:OnStart) { & $global:OnStart $TaskName }
}
function Get-ScheduledTask {
    param([string]$TaskName)
    $s = if ($global:PendingNow -gt 0) { 'Queued' } elseif ($global:TaskStates -and $global:TaskStates[$TaskName]) { $global:TaskStates[$TaskName] } else { 'Ready' }
    [pscustomobject]@{ TaskName = $TaskName; State = [TnwksTaskState]$s; Settings = [pscustomobject]@{ Enabled = ($s -ne 'Disabled') }; Triggers = $null }
}
function Start-Sleep { }
"""


def runner_output(tmp_path: Path, key: str, step: str, result: dict | None, code: int = 0) -> list:
    """The run of an account's task through run_task.yml and the runner, with the result file
    that the account script would write (None: no file)."""
    log_dir = tmp_path / f"log-{key}-{uuid.uuid4().hex[:6]}"
    log_dir.mkdir()
    if result is not None:
        # A time after the start of the runner: a result of this run.
        (log_dir / f"{step}.json").write_text(json.dumps({**result, "step": step, "time": 4102444800}))
    return run_script(tmp_path, "run_task.yml", "Run the task", {
        "Runner": str(RUNNER), "Task": f"tnwks-moverh-{key}-{step}", "Result": str(log_dir / f"{step}.json"), "Minutes": "20",
    }, stand_ins=TASKS + f"$global:Code = {code}\n")


# The files of the distro, as the play gives them here: their SHA-256s are what the check
# step reads back.
FILE_VARS = {"wsl_move_rehearsal_distribution_conf": "[shortcut]\n", "wsl_move_rehearsal_wsl_conf": "[user]\n"}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def check_result(disk: str, **over) -> dict:
    """The result of the check step as the account script writes it after the changes here:
    each value a number, a flag or one text."""
    out = {"registered": True, "oobe_complete": 1, "disk": disk, "default_distribution": True, "default_uid": 1000,
           "read_exit": 0, "repeated_count": 0, "repeated_keys": "", "uid": "1000", "user_uid": "1000",
           "token": "tnwks-move-rehearsal-data",
           "distribution_conf_sha256": sha256(FILE_VARS["wsl_move_rehearsal_distribution_conf"]),
           "wsl_conf_sha256": sha256(FILE_VARS["wsl_move_rehearsal_wsl_conf"]),
           "icon_sha256": DEFAULTS["wsl_move_rehearsal_icon_sha256"], "mem_total_kib": 1000000}
    out.update(over)
    return out


VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())
DIR = r"C:\ProgramData\tnwks-moverh"


# --- Setup: the stops of the procedure ----------------------------------------------------

def stops_shim(tmp_path: Path) -> Path:
    """A powershell.exe for the stops task: PowerShell 7 with the fakes of the window tests,
    which runs the procedure with the arguments that the task gives."""
    system_root = tmp_path / "SystemRoot"
    bin_dir = system_root / "System32/WindowsPowerShell/v1.0"
    bin_dir.mkdir(parents=True)
    (bin_dir / "shim.ps1").write_text(WINDOW_FAKES + f"""
$global:Sids = @{{ 'tnwks-moverh-a' = '{A_SID}'; 'tnwks-moverh-b' = '{B_SID}' }}
$global:Results = @{{}}; $global:Codes = @{{}}; $global:Acls = @{{}}
$a = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'argv.txt'))
# As powershell.exe may: a line on stderr, which the task keeps as text.
[Console]::Error.WriteLine('a line on stderr')
Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.txt') -Value ($a -join ' ')
$i = [array]::IndexOf($a, '-File')
$named = @{{}}
for ($j = $i + 2; $j -lt $a.Count; $j += 2) {{ $named[$a[$j].TrimStart('-')] = $a[$j + 1] }}
& $a[$i + 1] @named
exit [int]$LASTEXITCODE
""")
    shim = bin_dir / "powershell.exe"
    shim.write_text('#!/bin/sh\nd=$(dirname "$0")\nprintf \'%s\\n\' "$@" > "$d/argv.txt"\n'
                    'exec pwsh -NoProfile -NonInteractive -File "$d/shim.ps1"\n')
    shim.chmod(0o755)
    return system_root


def stops_config(tmp_path: Path, root: Path) -> tuple[str, dict]:
    """The test config as the play renders it (the TestConfig parameter), with the
    separators of this file system."""
    task = find_task("procedure_stops.yml", "The stops of the procedure")
    expression = task["ansible.windows.win_powershell"]["parameters"]["TestConfig"]
    [rendered] = run_plays(tmp_path, [{
        "vars": {"wsl_move_rehearsal_dir": str(root)},
        "tasks": [{"name": "render", "vars": {k: v for k, v in task["vars"].items() if not k.startswith("ansible_")},
                   "ansible.builtin.set_fact": {"tnwks_test_config": expression}}],
        "keep": ["tnwks_test_config"]}])
    assert not passed(rendered)
    text = rendered["facts"]["tnwks_test_config"]
    cfg = json.loads(text) if isinstance(text, str) else text
    return json.dumps(cfg).replace("\\\\", "/"), cfg


@needs_both
def test_the_stops_run_the_procedure_on_an_empty_state_file_and_the_check_passes(tmp_path):
    root = tmp_path / "rehearsal"
    root.mkdir()
    config, cfg = stops_config(tmp_path, root)
    # The paths of the test, and nothing else of the config, change.
    test_dir = f"{root}\\test"
    assert cfg["state_file"] == f"{test_dir}\\window.state" and cfg["version_file"] == f"{test_dir}\\wsl-version.txt"
    assert cfg["present"]["folder"] == f"{test_dir}\\a" and cfg["new"]["folder"] == f"{test_dir}\\b"
    assert cfg["present"]["account"] == "tnwks-moverh-a" and cfg["present"]["disable_tasks"] == ["tnwks-moverh-a-start"]
    assert cfg["restart"] is False and cfg["boot"] == {"group_steps": False}
    system_root = stops_shim(tmp_path)
    config = json.loads(config)
    config["service_binary"] = "__PSHOME__/System.Management.Automation.dll"
    output = run_script(tmp_path, "procedure_stops.yml", "The stops of the procedure", {
        "Script": str(WINDOW), "Test": str(root / "test"), "DiskName": "ext4.vhdx", "TestConfig": json.dumps(config),
    }, stand_ins=f"$env:SystemRoot = {ps_str(system_root)}\n", edits=(PSHOME_EDIT, SLASH_EDIT))
    by_case = {o["case"]: o for o in output}
    assert all("a line on stderr" in o["output"] for o in output), output
    assert set(by_case) == {"another version", "neither path", "both paths", "no started window"}
    assert " stop at step 1: WSL " in by_case["another version"]["last_log"]
    assert by_case["another version"]["last_log"].endswith(" is not the saved 0.0.0.0. Nothing is changed.")
    assert by_case["neither path"]["last_log"].endswith("stop: the disk file is at neither path. Nothing is changed.")
    assert by_case["both paths"]["last_log"].endswith("stop: the disk file is at both paths. Nothing is changed.")
    assert by_case["no started window"]["last_log"].endswith("no action: the last marker is 'wayback-done'")
    # The test folder goes at the end.
    assert not (root / "test").exists()
    [result] = run_plays(tmp_path, [{"vars": {"wsl_move_rehearsal_stops": registered(output)},
                                     "tasks": checks_of("procedure_stops.yml")}])
    assert not passed(result), passed(result)


@needs_pwsh
def test_the_stops_remove_the_test_folder_also_when_a_case_fails(tmp_path):
    root = tmp_path / "rehearsal"
    (root / "test").mkdir(parents=True)
    config = {"present": {"folder": str(root / "test/a")}, "new": {"folder": str(root / "test/b")},
              "state_file": str(root / "test/window.state"), "log_file": str(root / "test/window.log"),
              "acl_file": str(root / "test/disk.sddl"), "version_file": str(root / "test/wsl-version.txt"),
              "service_binary": "__PSHOME__/System.Management.Automation.dll"}
    # No powershell.exe at the path: the first case throws.
    error = run_script(tmp_path, "procedure_stops.yml", "The stops of the procedure", {
        "Script": str(WINDOW), "Test": str(root / "test"), "DiskName": "ext4.vhdx", "TestConfig": json.dumps(config),
    }, stand_ins=f"$env:SystemRoot = {ps_str(tmp_path / 'none')}\n",
        edits=(PSHOME_EDIT, SLASH_EDIT), fails=True)
    assert "powershell.exe" in error
    assert not (root / "test").exists()


@needs_pwsh
def test_an_empty_file_reads_as_an_empty_text_only_through_a_subexpression(tmp_path):
    # Get-Content -Raw of an empty file gives nothing, and a [string] cast of nothing
    # stays $null: a method call on it throws.
    (tmp_path / "empty").write_text("")
    run = pwsh(f"""
$cast = [string](Get-Content -LiteralPath {ps_str(tmp_path / 'empty')} -Raw)
$sub = "$(Get-Content -LiteralPath {ps_str(tmp_path / 'empty')} -Raw)"
[Console]::Out.Write("$($null -eq $cast) $($sub.Length)")
""")
    assert run.stdout == "True 0", run.stderr


def test_no_script_of_the_role_reads_a_file_through_a_string_cast():
    for path in [*(ROLE / "tasks").glob("*.yml"), *(ROLE / "files").glob("*.ps1")]:
        assert "[string](Get-Content" not in path.read_text(), path.name


class TaggedLoader(yaml.SafeLoader):
    """The safe loader, with each local tag (!vault, !unsafe) read as its plain value."""


TaggedLoader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)))


def test_each_condition_of_the_ansible_tree_is_a_text():
    # A condition with ": " in it loads as a mapping, and the task fails on the host.
    def conditions(node):
        if isinstance(node, dict):
            for key in ("ansible.builtin.assert", "assert"):
                if isinstance(node.get(key), dict):
                    that = node[key].get("that")
                    yield from that if isinstance(that, list) else [that]
            for key in ("failed_when", "changed_when", "when", "until"):
                if key in node:
                    yield from node[key] if isinstance(node[key], list) else [node[key]]
            for value in node.values():
                yield from conditions(value)
        elif isinstance(node, list):
            for value in node:
                yield from conditions(value)

    bad = []
    for path in sorted(p for d in ("roles", "playbooks") for p in (ANSIBLE / d).rglob("*.yml")):
        # A tag (!vault, !unsafe) reads as its plain value; any other error fails.
        doc = yaml.load(path.read_text(), Loader=TaggedLoader)
        bad += [f"{path.relative_to(ANSIBLE)}: {c!r}" for c in conditions(doc) if not isinstance(c, (str, bool))]
    assert bad == []


# --- Setup, window, B cycle: the reads of the check step ----------------------------------

@needs_both
def test_the_checks_of_the_reads_pass_on_the_results_that_the_runner_gives(tmp_path):
    disk_a = f"{DIR}\\a\\ext4.vhdx"
    good = runner_output(tmp_path, "a", "check", check_result(disk_a))
    repeated = runner_output(tmp_path, "a", "check", check_result(disk_a, repeated_count=1, repeated_keys="token"))
    # A run that wrote no result, and a refused run.
    none = runner_output(tmp_path, "b", "import", None, code=1)
    refused = runner_output(tmp_path, "b", "unregister", {"refused": "a file is at the path", "registered": True}, code=2)
    assert good[-1]["result"]["repeated_count"] == 0 and good[-1]["result"]["registered"] is True
    assert none[-1]["result"] is None and refused[-1]["code"] == 2
    setup = checks_of("setup.yml", ("A owns the distro",))
    plays = [
        {"vars": {"wsl_move_rehearsal_run": {"key": "a", "step": "check"}, "wsl_move_rehearsal_run_out": registered(good)},
         "tasks": checks_of("run_task.yml") + setup, "keep": ["wsl_move_rehearsal_runs"]},
        {"vars": {"wsl_move_rehearsal_run": {"key": "a", "step": "check"}, "wsl_move_rehearsal_run_out": registered(repeated)},
         "tasks": checks_of("run_task.yml") + setup},
        {"vars": {"wsl_move_rehearsal_run": {"key": "b", "step": "import", "expect": "not-run"},
                  "wsl_move_rehearsal_run_out": registered(none)}, "tasks": checks_of("run_task.yml")},
        {"vars": {"wsl_move_rehearsal_run": {"key": "b", "step": "unregister", "expect": "refused"},
                  "wsl_move_rehearsal_run_out": registered(refused)}, "tasks": checks_of("run_task.yml")},
        {"vars": {"wsl_move_rehearsal_run": {"key": "b", "step": "import"}, "wsl_move_rehearsal_run_out": registered(none)},
         "tasks": checks_of("run_task.yml")},
    ]
    ok, twice, not_run, refused_ok, no_result = run_plays(tmp_path, plays)
    assert not passed(ok), passed(ok)
    assert ok["facts"]["wsl_move_rehearsal_runs"]["check"][0]["result"]["token"] == "tnwks-move-rehearsal-data"
    # A key that came twice fails the check of setup.
    assert failed_by_assert(twice, "A owns the distro")
    assert not passed(not_run) and not passed(refused_ok)
    assert failed_by_assert(no_result, "failed_when of Run the task")


@needs_both
def test_a_task_that_can_not_start_is_not_run_also_after_an_earlier_good_run(tmp_path):
    """expect: not-run holds for each form of a task that did not start, with no result of
    this run: one that never ran (0x41303), one that Windows refuses at the start, and one
    that ran well before and shows no new run (its last result stays 0). A new run with
    exit 0 and a result is not a pass."""
    def no_start(name: str, start: str, code: int, result: dict | None) -> list:
        log_dir = tmp_path / f"log-{name}"
        log_dir.mkdir()
        if result is not None:
            # A result file of an earlier run: older than the start of the runner.
            (log_dir / "import.json").write_text(json.dumps({**result, "step": "import", "time": 1000}))
        return run_script(tmp_path, "run_task.yml", "Run the task", {
            "Runner": str(RUNNER), "Task": "tnwks-moverh-a-import", "Result": str(log_dir / "import.json"), "Minutes": "0",
        }, stand_ins=TASKS + f"$global:Code = {code}\nfunction Start-ScheduledTask {{ param([string]$TaskName) {start} }}\n")
    never = no_start("never", "", 0x41303, None)
    refused = no_start("refused", "throw 'The user account does not have permission to run this task.'", 0x41303, None)
    earlier = no_start("earlier", "", 0, {"disk": "old"})
    assert never[-1]["ran"] is False and never[-1]["code"] == 0x41303 and never[-1]["result"] is None
    assert refused[-1]["refused"] and refused[-1]["result"] is None
    assert earlier[-1]["ran"] is False and earlier[-1]["code"] == 0 and not earlier[-1]["refused"]
    assert earlier[-1]["result"] is None
    new_run = runner_output(tmp_path, "a", "import", {"disk": "new"})
    assert new_run[-1]["ran"] is True and new_run[-1]["code"] == 0 and new_run[-1]["result"] is not None
    # A new run with exit 0 and no result of its own: it started, so not a pass either.
    started = runner_output(tmp_path, "a", "import", None)
    assert started[-1]["ran"] is True and started[-1]["code"] == 0 and started[-1]["result"] is None
    expect = {"key": "a", "step": "import", "expect": "not-run"}
    results = run_plays(tmp_path, [{"vars": {"wsl_move_rehearsal_run": expect, "wsl_move_rehearsal_run_out": registered(out)},
                                    "tasks": checks_of("run_task.yml")} for out in (never, refused, earlier, new_run, started)])
    for name, result in zip(("never", "refused", "earlier"), results[:3]):
        assert not passed(result), (name, passed(result))
    for name, result in zip(("new run", "started"), results[3:]):
        assert failed_by_assert(result, "failed_when of Run the task"), (name, result)


ACCOUNT_TASK = ROLE / "files/Invoke-TnwksWslAccountTask.ps1"


def run_account_task(tmp_path: Path, step: str) -> tuple[int, dict | None]:
    """A run of the account task script for one step, with a config in a scope under
    tmp_path and a System32 folder with no program in it: the exit code and the result."""
    tag = uuid.uuid4().hex[:6]
    base = tmp_path / f"account-{tag}"
    for d in ("root/a", "log", "sys/System32"):
        (base / d).mkdir(parents=True)
    (base / "root/a/ext4.vhdx").write_text("disk")
    config = base / "config.json"
    config.write_text(json.dumps({
        "log_dir": str(base / "log"), "distro": "tnwks-moverh-test", "name_pattern": "^tnwks-moverh-",
        "refused_names": ["tnwks"], "root": str(base / "root"), "folder": str(base / "root/a"), "disk_name": "ext4.vhdx"}))
    run = pwsh(f"""$env:SystemRoot = {ps_str(base / 'sys')}
& {ps_str(ACCOUNT_TASK)} -Config {ps_str(config)} -Step {step}
"exit=$LASTEXITCODE"
""")
    code = int(run.stdout.strip().splitlines()[-1].removeprefix("exit="))
    result = base / "log" / f"{step}.json"
    return code, (json.loads(result.read_text()) if result.exists() else None)


@needs_pwsh
def test_an_error_that_a_step_does_not_handle_writes_a_failed_result_with_its_text(tmp_path):
    """No whoami.exe in System32: the first call of the step throws. The step ends with
    exit 1 and a result of this run, with the text of the error in failed, the key of each
    other failed step. A refusal (a disk file outside the scope) keeps exit 2."""
    for step in ("probe", "strip-rules"):
        code, result = run_account_task(tmp_path, step)
        assert code == 1, (step, code)
        assert result is not None and result["step"] == step, (step, result)
        assert "whoami.exe" in result["failed"] and result["failed"].startswith("the step stopped at line "), result
    account = ACCOUNT_TASK.read_text()
    assert account.count("trap {") == 1
    assert "if ($cfg) { Exit-Tnwks 1 @{ failed = " in account


def test_the_strip_step_writes_only_the_rules_and_no_provider_cmdlet_writes_rules():
    """The account runs as a standard user, with no privilege. Set-Acl keeps the audit
    section in its second try when the file has protected rules, and that write needs a
    privilege; .NET writes only the sections that changed. So the step reads and writes
    with [IO.File] and a new object that has the rules section only."""
    account = ACCOUNT_TASK.read_text()
    code = "\n".join(line for line in account.splitlines() if not line.lstrip().startswith("#"))
    for cmdlet in ("Set-Acl", "Get-Acl", "icacls"):
        assert cmdlet not in code, cmdlet
    write = re.search(r"function Set-TnwksFileRules\(\[string\]\$Path, \[string\]\$Sddl\) \{\n(.*?)\n\}", code, re.DOTALL)[1]
    assert [line.strip() for line in write.splitlines()] == [
        "$rules = New-Object Security.AccessControl.FileSecurity",
        "$rules.SetSecurityDescriptorSddlForm($Sddl, 'Access')",
        "[IO.File]::SetAccessControl($Path, $rules)"]
    read = re.search(r"function Get-TnwksFileSecurity\(\[string\]\$Path, \[string\]\$Sections\) \{\n(.*?)\n\}", code, re.DOTALL)[1]
    assert read.strip() == "[IO.File]::GetAccessControl($Path, [Security.AccessControl.AccessControlSections]$Sections)"
    strip = code[code.index("'strip-rules' {"):]
    assert "(Get-TnwksFileSecurity $target 'Owner')" in strip and "(Get-TnwksFileSecurity $target 'Access')" in strip
    assert 'Set-TnwksFileRules $target "D:P(A;;FA;;;$user)"' in strip
    # The step reads and writes the security of the file through these two only.
    for call in ("AccessControl", "New-Object Security"):
        assert call not in strip, call


def test_the_check_step_writes_the_repeated_keys_as_a_count_and_one_text():
    # The asserts on the count: test_the_role_reads_back_each_file_that_it_writes_in_the_distro.
    account = (ROLE / "files/Invoke-TnwksWslAccountTask.ps1").read_text()
    # A count and one text: no list goes into a result file.
    assert "$data.repeated_count = @($parsed.repeated).Count" in account
    assert "$data.repeated_keys = @($parsed.repeated) -join ','" in account
    assert "integrity_sids = $label -join ','" in account


# --- Window: the run of the procedure and the reads after it ------------------------------

PROCEDURE_STAND_INS = TASKS + r"""
# The run of the procedure: the start writes the lines that a real run wrote.
$global:OnStart = {
    param($TaskName)
    [IO.File]::AppendAllText($global:LogPath, [IO.File]::ReadAllText($global:RunLog))
    [IO.File]::AppendAllText($global:StatePath, [IO.File]::ReadAllText($global:RunState))
}
"""


def procedure_output(tmp_path: Path, run: dict, log_before: str, state_before: str, request: dict, last_markers: list,
                     hashes: list | None = None) -> list:
    """The output of run_procedure.yml for a run of the procedure (run_window) that starts
    from the log and the state file given."""
    root = Path(run["root"])
    files = tmp_path / f"proc-{uuid.uuid4().hex[:6]}"
    files.mkdir()
    (files / "window.log").write_text(log_before)
    (files / "window.state").write_text(state_before)
    full_log = (root / "state/window.log").read_text()
    full_state = (root / "state/window.state").read_text()
    # The lines of this run only: run_window starts from the same state.
    new_state = full_state.removeprefix(state_before)
    (files / "run.log").write_text(full_log)
    (files / "run.state").write_text(new_state)
    return run_script(tmp_path, "run_procedure.yml", "The window procedure", {
        "Task": "tnwks-moverh-window", "Request": str(files / "request.json"), "RequestJson": json.dumps(request),
        "Log": str(files / "window.log"), "State": str(files / "window.state"), "LastMarkers": last_markers,
        "Hashes": hashes if hashes is not None else [f"{sha256(WINDOW.read_text())} {WINDOW}"],
    }, stand_ins=PROCEDURE_STAND_INS + f"""
$global:LogPath = {ps_str(files / 'window.log')}; $global:StatePath = {ps_str(files / 'window.state')}
$global:RunLog = {ps_str(files / 'run.log')}; $global:RunState = {ps_str(files / 'run.state')}
""")


READ_WINDOW_STAND_INS = TASKS + r"""
$global:TaskStates = @{ 'tnwks-moverh-a-start' = 'Disabled'; 'tnwks-moverh-b-start' = 'Ready' }
function Get-Acl { param([string]$LiteralPath) [pscustomobject]@{ Owner = 'BUILTIN\Administrators' } }
function Get-LocalUser { param([string]$Name) [pscustomobject]@{ PasswordLastSet = [datetime]'2026-01-02T03:04:05' } }
"""


# The rules of the moved file as Windows writes them: B the owner, full control (FA).
MOVED_SDDL = f"O:{B_SID}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{B_SID})"
VM_ACE = "(A;;FA;;;S-1-5-83-1-1111-2222-3333-4444)"
CAPABILITY = DEFAULTS["wsl_move_rehearsal_vm_capability_sid"]
CAP_ACE = f"(A;;FA;;;{CAPABILITY})"


def disk_read(tmp_path: Path, rule: str, sddls: list[str], saved: str | None = None, wait: int = 0,
              owner: str = "new-account", mask: str = "FA", exists: bool = True, inherit: str = "") -> list:
    """The read of read_disk.yml with the functions of the procedure (from the role's
    copy), with the read of the file giving each SDDL of sddls in turn, one more for each
    wait (the last one stays), and a folder that gives the rules of inherit."""
    tag = uuid.uuid4().hex[:6]
    folder = tmp_path / f"folder-{tag}"
    folder.mkdir()
    disk = folder / "ext4.vhdx"
    if exists:
        disk.write_text("disk")
    saved_file = tmp_path / f"saved-{tag}.sddl"
    if saved is not None:
        saved_file.write_text(saved + "\r\n")
    stand_ins = WINDOW_FAKES + (
        "$global:ReadSeq = @{ " + ps_str(disk) + " = @(" + ", ".join(ps_str(x) for x in sddls) + ") }\n$global:ReadAt = 0\n"
        "$global:Inherit = @{ " + ps_str(folder) + " = " + ps_str(inherit) + " }\n"
        "function Start-Sleep { $global:ReadAt++ }\n")
    return run_script(tmp_path, "read_disk.yml", "Read the rules of the disk file", {
        "Path": str(disk), "Rule": rule, "Saved": str(saved_file), "Owner": owner, "Mask": mask,
        "SidA": A_SID, "SidB": B_SID, "Wait": wait, "Procedure": str(WINDOW), "Capability": CAPABILITY}, stand_ins=stand_ins)


NO_FILE = {"exists": False, "saved": False, "rules_ok": False, "vm": 0, "capability": 0, "deny": 0, "inherits_ok": False,
           "gives": "", "inherited_note": "", "owner": "", "sddl": ""}


@needs_pwsh
def test_the_read_of_the_disk_file_allows_the_rules_of_wsl_only_and_masks_each_account(tmp_path):
    moved = disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE + CAP_ACE])[-1]
    assert moved["rules_ok"] is True and moved["vm"] == 1 and moved["capability"] == 1, moved
    assert moved["inherits_ok"] is True and moved["gives"] == "" and moved["deny"] == 0
    assert moved["owner"] == "<b>"
    assert moved["sddl"] == f"O:<b>D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;<b>)(A;;FA;;;S-1-5-83-1-<vm>){CAP_ACE}"
    # A mask by its value: 0x1f01ff is FA.
    assert disk_read(tmp_path, "moved", [MOVED_SDDL.replace(";FA;;;" + B_SID, ";0x1F01FF;;;" + B_SID)])[-1]["rules_ok"] is True
    # Any other rule, another owner, a narrower mask, no protection, the rule of the
    # account replaced by a rule of a VM, two rules of the capability, a deny rule, or
    # a rule of WSL in another form: not a pass.
    other_cap = "S-1-15-3-1024-1-2-3-4-5-6-7-8"
    for sddl in (MOVED_SDDL + "(A;;FA;;;S-1-5-21-9-9-9-1003)", MOVED_SDDL.replace(f"O:{B_SID}", "O:BA"),
                 MOVED_SDDL.replace(f"(A;;FA;;;{B_SID})", f"(A;;0x1301bf;;;{B_SID})"), MOVED_SDDL.replace("D:P", "D:"),
                 MOVED_SDDL.replace(f"(A;;FA;;;{B_SID})", VM_ACE), MOVED_SDDL + CAP_ACE + CAP_ACE,
                 MOVED_SDDL + f"(A;;FA;;;{other_cap})", MOVED_SDDL + f"(D;;FA;;;{A_SID})",
                 MOVED_SDDL + VM_ACE.replace("(A;;FA;", "(D;;FA;"), MOVED_SDDL + VM_ACE.replace(";FA;", ";0x1301bf;"),
                 MOVED_SDDL + VM_ACE.replace("(A;;", "(A;OICI;"), MOVED_SDDL + "(A;;FA;;;S-1-5-83-1-1111-2222-3333-4444-5555)",
                 MOVED_SDDL + "(A;;FA;;;S-1-5-83-1-1111-2222-3333)", MOVED_SDDL + "(A;;FA;;;S-1-5-83-0-1111-2222-3333-4444)"):
        out = disk_read(tmp_path, "moved", [sddl])[-1]
        assert out["rules_ok"] is False, sddl
    deny = disk_read(tmp_path, "moved", [MOVED_SDDL + f"(D;;FA;;;{A_SID})"])[-1]
    assert deny["deny"] == 1 and deny["vm"] == 0
    # The VM SID has four numbers after S-1-5-83-1: one with five or three is not WSL's.
    for sid in ("S-1-5-83-1-1111-2222-3333-4444-5555", "S-1-5-83-1-1111-2222-3333"):
        out = disk_read(tmp_path, "moved", [MOVED_SDDL + f"(A;;FA;;;{sid})"])[-1]
        assert out["vm"] == 0 and out["rules_ok"] is False and sid in out["sddl"], out
    # The owner and the mask come from the config.
    admins = f"O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;{B_SID})"
    assert disk_read(tmp_path, "moved", [admins], owner="administrators", mask="0x1301bf")[-1]["rules_ok"] is True
    assert disk_read(tmp_path, "moved", [admins])[-1]["rules_ok"] is False
    # No file, or a read only: not a pass.
    assert disk_read(tmp_path, "moved", [""], exists=False)[-1] == NO_FILE
    assert disk_read(tmp_path, "none", [MOVED_SDDL])[-1]["rules_ok"] is False


@needs_pwsh
def test_the_read_of_the_saved_rules_compares_with_the_file_that_the_way_back_wrote(tmp_path):
    saved = f"O:{A_SID}D:P(A;;FA;;;BA)(A;;FA;;;{A_SID})"
    out = disk_read(tmp_path, "saved", [saved + VM_ACE], saved=saved)[-1]
    assert out["saved"] is True and out["rules_ok"] is True and out["vm"] == 1
    assert out["sddl"] == "O:<a>D:P(A;;FA;;;BA)(A;;FA;;;<a>)(A;;FA;;;S-1-5-83-1-<vm>)"
    # A stale rule of WSL in the saved text compares as no rule.
    assert disk_read(tmp_path, "saved", [saved], saved=saved + VM_ACE + CAP_ACE)[-1]["rules_ok"] is True
    assert disk_read(tmp_path, "saved", [saved.replace(f"O:{A_SID}", "O:BA")], saved=saved)[-1]["rules_ok"] is False
    assert disk_read(tmp_path, "saved", [saved])[-1] | {"sddl": ""} == NO_FILE | {
        "exists": True, "inherits_ok": True, "owner": "<a>"}


FOLDER_A = "(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;CO)(A;ID;FR;;;AU)"


@needs_pwsh
def test_the_read_of_the_saved_rules_compares_the_explicit_rules_and_reads_the_inherited_ones_apart(tmp_path):
    # A file of an older WSL: no protection, the rules of its folder (ID), one explicit rule.
    saved = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;{A_SID})(A;ID;FR;;;AU)"
    out = disk_read(tmp_path, "saved", [saved + VM_ACE], saved=saved, inherit=FOLDER_A)[-1]
    assert out["rules_ok"] is True and out["inherits_ok"] is True and out["inherited_note"] == "", out
    assert out["gives"] == "A;ID;0x120089;;;AU A;ID;0x1301bf;;;<a> A;ID;0x1f01ff;;;BA A;ID;0x1f01ff;;;SY"
    # An explicit rule in the place of an inherited one, protection, another owner or
    # another explicit rule: not a pass.
    for sddl in (saved.replace("(A;ID;FR;;;AU)", "(A;;FR;;;AU)"), saved.replace("D:AI", "D:PAI"),
                 saved.replace(f"O:{A_SID}", "O:BA"), saved.replace(f"(A;;FA;;;{A_SID})", f"(A;;0x1301bf;;;{A_SID})")):
        out = disk_read(tmp_path, "saved", [sddl], saved=saved, inherit=FOLDER_A)[-1]
        assert out["rules_ok"] is False, sddl
    # Inherited rules other than the folder gives (one gone, or the rule of CREATOR
    # OWNER for another account): the saved owner, protection and explicit rules
    # pass, and inherits_ok is false, with the note of the difference.
    for sddl in (saved.replace("(A;ID;FR;;;AU)", ""), saved.replace(f"(A;ID;0x1301bf;;;{A_SID})", "(A;ID;0x1301bf;;;BA)")):
        out = disk_read(tmp_path, "saved", [sddl], saved=saved, inherit=FOLDER_A)[-1]
        assert out["rules_ok"] is True and out["inherits_ok"] is False and out["inherited_note"], sddl
    # A saved set with the flag AI and no inherited rule, as the restore at the old path
    # leaves it: the folder gives its rules, and the difference from the saved set is a
    # note, not a failed check.
    bare = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})"
    out = disk_read(tmp_path, "saved", [saved], saved=bare, inherit=FOLDER_A)[-1]
    assert out["rules_ok"] is True and out["inherits_ok"] is True, out
    assert out["inherited_note"] == ("the inherited rules differ from the saved set: saved '', now "
                                     "'A;ID;0x120089;;;AU A;ID;0x1301bf;;;<a> A;ID;0x1f01ff;;;BA A;ID;0x1f01ff;;;SY'")
    # A file with no inherited rule in a folder that gives some (the form of a file
    # that moved in from a folder that gives none), against a saved set with the same
    # owner and explicit rules: rules_ok, and inherits_ok is false.
    out = disk_read(tmp_path, "saved", [bare], saved=saved, inherit=FOLDER_A)[-1]
    assert out["rules_ok"] is True and out["inherits_ok"] is False, out
    assert disk_read(tmp_path, "none", [bare], inherit=FOLDER_A)[-1]["inherits_ok"] is False


@needs_pwsh
def test_the_read_after_a_clean_stop_waits_for_the_rules_of_wsl_to_go(tmp_path):
    gone = disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE + CAP_ACE, MOVED_SDDL + CAP_ACE, MOVED_SDDL], wait=60)[-1]
    assert gone["vm"] == 0 and gone["capability"] == 0 and gone["rules_ok"] is True
    # With no wait, the first read; a rule that stays ends the wait at its limit.
    assert disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE, MOVED_SDDL])[-1]["vm"] == 1
    assert disk_read(tmp_path, "moved", [MOVED_SDDL + CAP_ACE], wait=1)[-1]["capability"] == 1


BEFORE_STAND_INS = TASKS + r"""
function secedit.exe {
    $cfg = $args[[array]::IndexOf($args, '/cfg') + 1]
    Set-Content -LiteralPath $cfg -Value $global:Policy
    $global:LASTEXITCODE = 0
}
"""


def before_read(tmp_path: Path, state: str, batch: str = f"*S-1-5-32-544,*{A_SID}", disks: tuple[str, ...] = ("a",),
                disabled: bool = False, tasks: dict | None = None) -> list:
    """The read of the state before the window (window.yml)."""
    root = tmp_path / f"before-{uuid.uuid4().hex[:6]}"
    for sub in ("a", "b", "side", "state"):
        (root / sub).mkdir(parents=True)
    for folder in disks:
        (root / folder / "ext4.vhdx").write_text("disk")
    (root / "state/window.state").write_text(state)
    if disabled:
        (root / "state/window.state.disabled").write_text("tnwks-moverh-a-start")
    states = {"tnwks-moverh-a-start": "Ready", "tnwks-moverh-b-start": "Disabled", **(tasks or {})}
    return run_script(tmp_path, "window.yml", "Read the state before the window", {
        "State": str(root / "state/window.state"), "Prefix": "tnwks-moverh", "DiskA": str(root / "a/ext4.vhdx"),
        "DiskB": str(root / "b/ext4.vhdx"), "DiskSide": str(root / "side/ext4.vhdx"), "SidA": A_SID, "NameA": "tnwks-moverh-a",
    }, stand_ins=BEFORE_STAND_INS + f"$env:TEMP = {ps_str(root)}\n"
        + f"$global:Policy = @('[Privilege Rights]', {ps_str('SeBatchLogonRight = ' + batch)})\n"
        + "$global:TaskStates = @{ " + "; ".join(f"{ps_str(k)} = {ps_str(v)}" for k, v in states.items()) + " }\n")


@needs_both
def test_the_window_starts_after_setup_or_after_a_way_back_and_on_no_other_state(tmp_path):
    first = before_read(tmp_path, "")
    assert first[-1] == {"last_marker": "", "disabled_list": False, "disk_a": True, "disk_b": False, "disk_side": False,
                         "batch_a": True, "start_a": "Ready", "start_a_enabled": True, "start_b": "Disabled",
                         "start_b_enabled": False}
    second = before_read(tmp_path, "t window-started\r\nt wayback-done\r\n", batch="*S-1-5-32-544,tnwks-moverh-a",
                         tasks={"tnwks-moverh-a-start": "Running"})
    assert second[-1]["last_marker"] == "wayback-done" and second[-1]["batch_a"] is True
    wrong = {
        "started": before_read(tmp_path, "t window-started\r\n", disks=("b",)),
        "done": before_read(tmp_path, "t window-started\r\nt window-done\r\n"),
        "no right": before_read(tmp_path, "", batch="*S-1-5-32-544"),
        "list": before_read(tmp_path, "t window-started\r\nt wayback-done\r\n", disabled=True),
        "both": before_read(tmp_path, "", disks=("a", "b")),
        "side": before_read(tmp_path, "", disks=("a", "side")),
        "b enabled": before_read(tmp_path, "", tasks={"tnwks-moverh-b-start": "Ready"}),
        "a disabled": before_read(tmp_path, "", tasks={"tnwks-moverh-a-start": "Disabled"}),
    }
    check_a = runner_output(tmp_path, "a", "check", check_result(f"{DIR}\\a\\ext4.vhdx"))
    check_b = runner_output(tmp_path, "b", "check", {"registered": False, "oobe_complete": 1})
    saved = f"O:{A_SID}D:P(A;;FA;;;BA)(A;;FA;;;{A_SID})"
    as_saved = disk_read(tmp_path, "saved", [saved + VM_ACE], saved=saved)
    other = disk_read(tmp_path, "saved", [saved.replace(f"O:{A_SID}", "O:BA")], saved=saved)
    no_saved = disk_read(tmp_path, "saved", [saved])
    # A's file in the form with no inherited rule (D:AI, one explicit rule), against a
    # saved set of an older WSL: the saved owner and explicit rules pass, whatever the
    # inherited rules; another explicit rule or another owner does not.
    older = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1301bf;;;{A_SID})(A;ID;FR;;;AU)"
    bare = f"O:{A_SID}D:AI(A;;FA;;;{A_SID})"
    bare_ok = disk_read(tmp_path, "saved", [bare], saved=older, inherit=FOLDER_A)
    assert bare_ok[-1]["rules_ok"] is True and bare_ok[-1]["inherits_ok"] is False, bare_ok
    bare_rule = disk_read(tmp_path, "saved", [bare + "(A;;FR;;;AU)"], saved=older, inherit=FOLDER_A)
    bare_owner = disk_read(tmp_path, "saved", [bare.replace(f"O:{A_SID}", "O:BA")], saved=older, inherit=FOLDER_A)
    names = ("The window starts after setup", "After a way back, A's disk file had its saved rules",
             "A owns the distro at its path")

    def play(before, disk, checks=(check_a, check_b)):
        return {"vars": {"wsl_move_rehearsal_before": registered(before), "wsl_move_rehearsal_disk_last": disk[-1],
                         "wsl_move_rehearsal_runs": {"check": [c[-1] for c in checks]}},
                "tasks": checks_of("window.yml", names)}

    no_start = runner_output(tmp_path, "a", "check", check_result(f"{DIR}\\a\\ext4.vhdx", read_exit=1))
    plays = [play(first, no_saved), play(second, as_saved), play(second, other), play(second, no_saved),
             play(first, no_saved, (check_a, runner_output(tmp_path, "b", "check", check_result(f"{DIR}\\b\\ext4.vhdx")))),
             play(first, no_saved, (no_start, check_b)),
             *[play(w, no_saved) for w in wrong.values()],
             play(second, bare_ok), play(second, bare_rule), play(second, bare_owner)]
    # After the rules of an older WSL, the file must have the inherited rules of its folder.
    shaped = "A's disk file has the inherited rules that its folder gives, right after the rules of an older WSL"
    plays += [{"vars": {"wsl_move_rehearsal_disk": registered([read[-1]])}, "tasks": checks_of("window.yml", (shaped,))}
              for read in (as_saved, bare_ok)]
    results = run_plays(tmp_path, plays)
    assert not passed(results[0]), passed(results[0])
    assert not passed(results[1]), passed(results[1])
    # After a way back, the file had its saved rules.
    for result in results[2:4]:
        assert failed_by_assert(result, names[1]) and not failed_by_assert(result, names[0])
        assert not failed_by_assert(result, names[2])
    # B lists no distro; A's distro starts with the rules of an older WSL.
    for result in results[4:6]:
        assert failed_by_assert(result, names[2]) and not failed_by_assert(result, names[1])
    for name, result in zip(wrong, results[6:6 + len(wrong)], strict=True):
        assert failed_by_assert(result, names[0]), name
    bare_results = results[6 + len(wrong):9 + len(wrong)]
    assert not passed(bare_results[0]), passed(bare_results[0])
    for result in bare_results[1:]:
        assert failed_by_assert(result, names[1]) and not failed_by_assert(result, names[0])
    assert not passed(results[-2]), passed(results[-2])
    assert failed_by_assert(results[-1], shaped)


# The rules of a file as .NET gives them (FileSecurity), kept in a JSON file between runs:
# the protection and the explicit rules, each a SID, a right, a type and its inheritance.
# A SID that is not one (not S-1-...) throws, as Windows does.
def older_run(tmp_path: Path, folder: Path, sddl: str | None = None) -> tuple[bool, str, list]:
    """The script that gives A's disk file the rules of an older WSL, with the functions
    of the procedure: gives whether it changed, the rules after it, and each write."""
    disk = folder / "ext4.vhdx"
    disk.write_text("disk")
    acls = tmp_path / "acls.json"
    if sddl is not None:
        acls.write_text(json.dumps({str(disk): sddl}))
    stand_ins = WINDOW_FAKES + (
        f"$global:Inherit = @{{ {ps_str(folder)} = {ps_str(FOLDER_A)} }}\n"
        f"$global:Acls = @{{}}; foreach ($p in (Get-Content -LiteralPath {ps_str(acls)} -Raw | ConvertFrom-Json).PSObject.Properties) {{\n"
        "    $global:Acls[$p.Name] = $p.Value }\n")
    tail = (f"\n[IO.File]::WriteAllText({ps_str(acls)}, (@{{ {ps_str(disk)} = $global:Acls[{ps_str(disk)}] }} | ConvertTo-Json))\n"
            f"[IO.File]::AppendAllLines({ps_str(tmp_path / 'writes.txt')}, [string[]]@($global:SecCalls))\n")
    params = {"Path": str(disk), "Sid": A_SID, "Procedure": str(WINDOW), "Capability": CAPABILITY}
    name = "A's disk file like a file of an older WSL, with the rules of its folder"
    _out, changed = run_script(tmp_path, "older_disk.yml", name, params, stand_ins=stand_ins, changed=True,
                              edits=(("$after = Get-TnwksSddlParts", tail + "$after = Get-TnwksSddlParts"),))
    writes = (tmp_path / "writes.txt").read_text().splitlines() if (tmp_path / "writes.txt").exists() else []
    return changed, json.loads(acls.read_text())[str(disk)], writes


@needs_pwsh
def test_the_older_form_of_a_s_disk_file_is_set_once_and_then_changes_nothing(tmp_path):
    """The script of older_disk.yml on the file that a newer WSL makes (A the owner, full
    control for A and the Administrators, protected), with the rules of WSL while A's VM
    runs: the first run takes the ownership for the Administrators, then writes the owner
    and the rules in one call, with no protection, so the file takes the rules that its
    folder gives; the rules of WSL stay. A second run reports no change and writes
    nothing."""
    folder = tmp_path / "a"
    folder.mkdir()
    newer = f"O:{A_SID}D:P(A;;FA;;;{A_SID})(A;;FA;;;BA){VM_ACE}{CAP_ACE}"
    changed, after, writes = older_run(tmp_path, folder, newer)
    assert changed is True
    assert writes == ["owner a/ext4.vhdx BA", f"rules a/ext4.vhdx O:{A_SID}D:AI(A;;FA;;;{A_SID}){VM_ACE}{CAP_ACE}"]
    assert after == (f"O:{A_SID}D:AI(A;;FA;;;{A_SID}){VM_ACE}{CAP_ACE}"
                     + FOLDER_A.replace(";;;CO)", f";;;{A_SID})"))
    (tmp_path / "writes.txt").unlink()
    changed, again, writes = older_run(tmp_path, folder)
    assert changed is False and again == after and writes == []
    # A file with the explicit rule and no protection, but not the inherited rules that
    # its folder gives (a write that set the rules before the owner): not already so.
    (tmp_path / "writes.txt").unlink(missing_ok=True)
    wrong = after.replace(f"(A;ID;0x1301bf;;;{A_SID})", "(A;ID;0x1301bf;;;BA)")
    changed, fixed, writes = older_run(tmp_path, folder, wrong)
    assert changed is True and fixed == after and len(writes) == 2


@needs_pwsh
def test_a_write_of_the_older_form_that_fails_puts_the_owner_back(tmp_path):
    """The take of ownership comes before the write of the rules. A write that fails puts
    A back as the owner, so the file keeps its owner and its old rules, and the task fails
    with a message that says so."""
    folder = tmp_path / "a"
    folder.mkdir()
    disk = folder / "ext4.vhdx"
    disk.write_text("disk")
    log = tmp_path / "sec.log"
    newer = f"O:{A_SID}D:P(A;;FA;;;{A_SID})(A;;FA;;;BA)"
    stand_ins = WINDOW_FAKES + (
        f"$global:Inherit = @{{ {ps_str(folder)} = {ps_str(FOLDER_A)} }}\n"
        f"$global:Acls = @{{ {ps_str(disk)} = {ps_str(newer)} }}\n"
        f"$global:FailRules = $true; $global:SecLog = {ps_str(log)}\n")
    params = {"Path": str(disk), "Sid": A_SID, "Procedure": str(WINDOW), "Capability": CAPABILITY}
    err = run_script(tmp_path, "older_disk.yml", "A's disk file like a file of an older WSL, with the rules of its folder",
                     params, stand_ins=stand_ins, fails=True)
    assert "failed (a write that fails). Its owner is back, with its old rules." in err, err
    assert log.read_text().splitlines() == ["owner a/ext4.vhdx BA", f"owner a/ext4.vhdx {A_SID}"]


@needs_pwsh
def test_no_script_of_the_role_has_two_variables_that_differ_only_by_case(tmp_path):
    """PowerShell names ignore case: a local $sid is the parameter $Sid. Per function (or
    the script itself), each variable has one spelling, in each embedded script of the
    task files and each script file of the role."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for file in sorted(p.name for p in (ROLE / "tasks").glob("*.yml")):
        for i, task in enumerate(tasks_in(file)):
            module = task.get("ansible.windows.win_powershell")
            if isinstance(module, dict) and isinstance(module.get("script"), str):
                (scripts / f"{Path(file).stem}--{i:03d}.ps1").write_text(module["script"])
    for script in (ROLE / "files").glob("*.ps1"):
        (scripts / script.name).write_text(script.read_text())
    run = pwsh(f"""
$skip = @('_', 'args', 'true', 'false', 'null', 'input', 'this', 'psitem', 'matches', 'lastexitcode', 'erroractionpreference',
    'pscmdlet', 'psboundparameters', 'ansible')
$found = @()
foreach ($f in Get-ChildItem -LiteralPath {ps_str(scripts)} -Filter *.ps1) {{
    $ast = [Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$null)
    $groups = @{{}}
    foreach ($v in $ast.FindAll({{ $args[0] -is [Management.Automation.Language.VariableExpressionAst] }}, $true)) {{
        if ($v.VariablePath.DriveName -and $v.VariablePath.DriveName -notin @('script', 'global', 'local', 'private', 'using')) {{ continue }}
        $name = $v.VariablePath.UserPath -replace '^(script|global|local|private|using):', ''
        if ($name.ToLowerInvariant() -in $skip) {{ continue }}
        $p = $v.Parent; while ($p -and $p -isnot [Management.Automation.Language.FunctionDefinitionAst]) {{ $p = $p.Parent }}
        $key = if ($p) {{ "function $($p.Name)" }} else {{ 'script' }}
        if (-not $groups[$key]) {{ $groups[$key] = @{{}} }}
        $low = $name.ToLowerInvariant()
        if (-not $groups[$key][$low]) {{ $groups[$key][$low] = New-Object System.Collections.Generic.HashSet[string] }}
        [void]$groups[$key][$low].Add($name)
    }}
    foreach ($k in $groups.Keys) {{ foreach ($low in $groups[$k].Keys) {{
        if ($groups[$k][$low].Count -gt 1) {{ $found += '{{0}} [{{1}}]: {{2}}' -f $f.Name, $k, (@($groups[$k][$low]) -join ' ') }} }} }}
}}
$found -join "`n"
""")
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "", run.stdout
    assert len(list(scripts.glob("*.ps1"))) > 40


def test_setup_and_the_window_give_a_s_disk_file_the_rules_of_an_older_wsl_from_one_task_file():
    def names(file):
        return [t.get("name", "") for t in tasks_in(file)]

    def includes(file):
        return [t.get("name", "") for t in tasks_in(file) if t.get("ansible.builtin.include_tasks") == "older_disk.yml"]

    assert includes("setup.yml") == ["A's disk file like a file of an older WSL"]
    assert includes("window.yml") == ["A's disk file like a file of an older WSL"]
    setup = names("setup.yml")
    assert (setup.index("A imports the distro into its folder") < setup.index("A's disk file like a file of an older WSL")
            < setup.index("A reads its distro"))
    # In the window: the read against the saved rules, the read of the state and
    # their asserts, then the rules of an older WSL, then A's and B's reads and their
    # assert.
    window = names("window.yml")
    # Every read and check of the start state comes before the first change.
    order = ["The rules of A's disk file as the last way back left them", "Keep the read of A's disk file before its rules change",
             "Read the state before the window", "The window starts after setup or after a way back",
             "After a way back, A's disk file had its saved rules", "A's disk file like a file of an older WSL",
             "A reads its distro before the window", "B lists no distro before the window", "A owns the distro at its path"]
    at = [next(i for i, n in enumerate(window) if n.startswith(o)) for o in order]
    assert at == sorted(at), at
    # One text of the rule of the folder, in setup and in the task file.
    older = yaml.safe_load((ROLE / "tasks/older_disk.yml").read_text())
    folder = next(t for t in older if t["name"].startswith("The folder of A"))
    assert folder["ansible.windows.win_powershell"]["parameters"]["Rules"] == [
        "{{ wsl_move_rehearsal_older_folder_rule }}", "(A;;{{ wsl_move_rehearsal_folder_rights.a }};;;{{ wsl_move_rehearsal_sids.a }})"]
    setup_folder = find_task("setup.yml", "Folders of the disk file")["ansible.windows.win_powershell"]["parameters"]
    assert "wsl_move_rehearsal_older_folder_rule" in setup_folder["Extra"]
    # Each change of the task file runs as SYSTEM, after its assert of the state.
    tasks = [t["name"] for t in older]
    assert tasks.index("The rules of A's disk file change only after setup or after a way back") < tasks.index(folder["name"])
    for task in older[2:]:
        assert task["vars"]["ansible_become_user"] == "SYSTEM", task["name"]


@needs_both
def test_the_rules_of_an_older_wsl_change_only_after_setup_or_after_a_way_back(tmp_path):
    states = {"setup": "", "way back": "t window-started\r\nt wayback-done\r\n",
              "started": "t window-started\r\n", "done": "t window-started\r\nt window-done\r\n",
              "boot": "t window-started\r\nt window-done\r\nt wayback-started\r\n"}
    plays = []
    for name, text in states.items():
        state = tmp_path / f"{name.replace(' ', '-')}.state"
        state.write_text(text)
        read = run_script(tmp_path, "older_disk.yml", "Read the last marker", {"State": str(state)})
        plays.append({"vars": {"wsl_move_rehearsal_older_marker": registered(read)},
                      "tasks": checks_of("older_disk.yml")})
    results = run_plays(tmp_path, plays)
    for name, result in zip(states, results, strict=True):
        if name in ("setup", "way back"):
            assert not passed(result), (name, passed(result))
        else:
            assert failed_by_assert(result, "The rules of A's disk file change only"), name


@needs_both
def test_the_window_checks_pass_on_a_run_from_empty_files_and_the_reads_after_it(tmp_path):
    request = {"mode": "forward", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True}
    run = run_window(tmp_path / "w", request, disk=("a",), saved_version="none", acl_file=None)
    assert run["code"] == 0, run["log"]
    # The window starts from the files that setup left: both empty.
    procedure = procedure_output(tmp_path, run, "", "", request, ["", "wayback-done"])
    assert procedure[-1]["markers"] == ["window-started", "window-done"]
    assert procedure[-1]["groups"] == ["joined", "left"]
    folder_b = tmp_path / "b"
    folder_b.mkdir()
    (folder_b / "ext4.vhdx").write_text("disk")
    (folder_b / "tnwks-move.marker").write_text("m")
    (folder_b / "shortcut.ico").write_text("icon")
    folder_a = tmp_path / "a"
    folder_a.mkdir()
    (folder_a / "tnwks-move.marker").write_text("m")
    log_b = tmp_path / "log-b"
    log_b.mkdir()
    (log_b / "import.json").write_text(json.dumps({"imported": True, "disk": str(folder_b / "ext4.vhdx"), "guid": "{x}",
                                                   "uid_after_import": "0", "uid_exit": 0, "step": "import", "time": 1}))
    read = run_script(tmp_path, "read_window.yml", "Read the folders", {
        "FolderA": str(folder_a), "FolderB": str(folder_b), "LogB": str(log_b), "NameB": "tnwks-moverh-b",
        "DiskName": "ext4.vhdx", "MarkerName": "tnwks-move.marker", "Prefix": "tnwks-moverh",
    }, stand_ins=READ_WINDOW_STAND_INS)
    windowed = read[-1]
    assert windowed["window_tasks_enabled_no_trigger"] is True, windowed
    assert windowed["icon_owner_b"] == ["shortcut.ico owner BUILTIN\\Administrators"]
    assert windowed["import_b"]["uid_after_import"] == "0"
    check = runner_output(tmp_path, "b", "check", check_result(f"{DIR}\\b\\ext4.vhdx"))
    facts = {
        "wsl_move_rehearsal_procedure_run": registered(procedure),
        "wsl_move_rehearsal_request": request,
        "wsl_move_rehearsal_runs": {"check": [check[-1]]},
        "wsl_move_rehearsal_window_read": registered(read),
        "wsl_move_rehearsal_b_password": registered([windowed["password_b"]]),
        "wsl_move_rehearsal_disk": registered(disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE])),
    }
    running = disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE])
    stopped = disk_read(tmp_path, "moved", [MOVED_SDDL + VM_ACE, MOVED_SDDL], wait=60)
    started = [{"running": True, "wsl": 1}]
    plays = [{"vars": facts, "tasks": checks_of("run_procedure.yml") + checks_of("window.yml", ("B owns the distro",))},
             {"vars": {"wsl_move_rehearsal_disk": registered(running), "wsl_move_rehearsal_b_started": registered(started)},
              "tasks": checks_of("window.yml", ("While B's VM runs", "failed_when of The start task of B"))},
             {"vars": {"wsl_move_rehearsal_disk": registered(stopped)}, "tasks": checks_of("window.yml", ("After the clean stop",))},
             # While the VM runs, a rule of the VM; after a clean stop, none.
             {"vars": {"wsl_move_rehearsal_disk": registered(disk_read(tmp_path, "moved", [MOVED_SDDL]))},
              "tasks": checks_of("window.yml", ("While B's VM runs",))},
             {"vars": {"wsl_move_rehearsal_disk": registered(running)}, "tasks": checks_of("window.yml", ("After the clean stop",))}]
    ok, run_ok, stop_ok, no_vm, vm_left = run_plays(tmp_path, plays)
    for result in (ok, run_ok, stop_ok):
        assert not passed(result), passed(result)
    assert failed_by_assert(no_vm, "While B's VM runs")
    assert failed_by_assert(vm_left, "After the clean stop")


@needs_pwsh
def test_a_window_task_with_no_trigger_or_an_empty_list_reads_as_no_trigger(tmp_path):
    out = run_script(tmp_path, "read_window.yml", "Read the folders", {
        "FolderA": str(tmp_path), "FolderB": str(tmp_path), "LogB": str(tmp_path), "NameB": "b",
        "DiskName": "ext4.vhdx", "MarkerName": "m", "Prefix": "tnwks-moverh"},
        stand_ins=READ_WINDOW_STAND_INS + r"""
$global:Triggers = @{ 'tnwks-moverh-a-stop' = @(); 'tnwks-moverh-b-import' = $null }
function Get-ScheduledTask {
    param([string]$TaskName)
    $t = if ($global:Triggers.ContainsKey($TaskName)) { $global:Triggers[$TaskName] } elseif ($global:WithTrigger -eq $TaskName) { @([pscustomobject]@{ Enabled = $true }) } else { $null }
    [pscustomobject]@{ State = [TnwksTaskState]'Ready'; Settings = [pscustomobject]@{ Enabled = $true }; Triggers = $t }
}
""" + f"[IO.File]::WriteAllText({ps_str(tmp_path / 'import.json')}, '{{}}')\n")
    assert out[-1]["window_tasks_enabled_no_trigger"] is True
    with_trigger = run_script(tmp_path, "read_window.yml", "Read the folders", {
        "FolderA": str(tmp_path), "FolderB": str(tmp_path), "LogB": str(tmp_path), "NameB": "b",
        "DiskName": "ext4.vhdx", "MarkerName": "m", "Prefix": "tnwks-moverh"},
        stand_ins=READ_WINDOW_STAND_INS + r"""
function Get-ScheduledTask {
    param([string]$TaskName)
    $t = if ($TaskName -eq 'tnwks-moverh-a-import') { @([pscustomobject]@{ Enabled = $true }) } else { $null }
    [pscustomobject]@{ State = [TnwksTaskState]'Ready'; Settings = [pscustomobject]@{ Enabled = $true }; Triggers = $t }
}
""")
    assert with_trigger[-1]["window_tasks_enabled_no_trigger"] is False


# --- Wayback: the VM processes against the baseline, and the tokens -----------------------

def cim_stand_in(processes: list[tuple[int, str, str | None]]) -> str:
    rows = ", ".join(
        f"[pscustomobject]@{{ ProcessId = {pid}; Name = '{name}'; CreationDate = "
        + (f"[datetime]::Parse('{created}', $null, 'RoundtripKind').ToLocalTime()" if created else "$null") + " }"
        for pid, name, created in processes)
    return f"function Get-CimInstance {{ param([string]$ClassName) @({rows}) }}\n"


LIVE_STAND_INS = r"""
function netsh.exe { 'Listen on ipv4:             Connect to ipv4:'; ''; '0.0.0.0         2222        172.20.0.2      22' }
function Get-NetNat { [pscustomobject]@{ Name = 'WSLNat'; InternalIPInterfaceAddressPrefix = '172.20.0.0/16' } }
function Get-LocalUser { param([string]$Name) $null }
$global:Clock = [datetime]'2026-01-01'
function Get-Date { $global:Clock = $global:Clock.AddSeconds(50); $global:Clock }
function Start-Sleep { }
"""

VMS = [(4242, "vmmemWSL", "2026-03-04T05:06:07.0000000Z"), (4343, "vmmem", "2026-03-04T05:06:09.0000000Z")]


def live_and_baseline(tmp_path: Path, processes) -> tuple[list, Path]:
    live = run_script(tmp_path, "live.yml", "Read the live parts", {"Script": str(LIVE), "PresentUser": "nobody"},
                      stand_ins=LIVE_STAND_INS + cim_stand_in(processes))
    baseline = tmp_path / f"baseline-{uuid.uuid4().hex[:6]}.json"
    # As the play keeps it: the output of the read, through to_json.
    baseline.write_text(json.dumps(live[-1]))
    return live, baseline


@needs_both
def test_the_wayback_poll_reads_the_baseline_as_the_play_keeps_it(tmp_path):
    live, baseline = live_and_baseline(tmp_path, VMS)
    # Two levels: each VM process is a text in the output and in the baseline.
    assert live[-1]["vm"] == ["@{id=4242; name=vmmemWSL; created=2026-03-04T05:06:07.0000000Z}",
                              "@{id=4343; name=vmmem; created=2026-03-04T05:06:09.0000000Z}"]
    assert live[-1]["registrations"] == "not read: the hive is not loaded"
    read_base = run_script(tmp_path, "live.yml", "Read the baseline", {"Path": str(baseline)})

    def poll(processes):
        return run_script(tmp_path, "wayback.yml", "No VM of the rehearsal", {"Path": str(baseline)},
                          stand_ins=LIVE_STAND_INS + cim_stand_in(processes))

    same = poll(VMS)
    assert same[-1]["same"] is True and same[-1]["baseline"] == same[-1]["now"], same
    assert same[-1]["baseline"] == ["4242 2026-03-04T05:06:07.0000000Z", "4343 2026-03-04T05:06:09.0000000Z"]
    # A VM of the rehearsal that is still there, and one that ended while another started.
    extra = poll([*VMS, (5000, "vmmem", "2026-03-04T06:00:00.0000000Z")])
    swapped = poll([VMS[0], (5000, "vmmem", "2026-03-04T06:00:00.0000000Z")])
    assert extra[-1]["same"] is False and swapped[-1]["same"] is False
    live_now = run_script(tmp_path, "live.yml", "Read the live parts", {"Script": str(LIVE), "PresentUser": "nobody"},
                          stand_ins=LIVE_STAND_INS + cim_stand_in(VMS))
    changed = run_script(tmp_path, "live.yml", "Read the live parts", {"Script": str(LIVE), "PresentUser": "nobody"},
                         stand_ins=LIVE_STAND_INS + cim_stand_in([VMS[0]]))
    plays = [
        {"vars": {"wsl_move_rehearsal_vm_count": registered(same)}, "tasks": checks_of("wayback.yml", ("No VM of the rehearsal",))},
        {"vars": {"wsl_move_rehearsal_vm_count": registered(extra)}, "tasks": checks_of("wayback.yml", ("No VM of the rehearsal",))},
        {"vars": {"wsl_move_rehearsal_live": registered(live_now), "wsl_move_rehearsal_baseline": registered(read_base)},
         "tasks": checks_of("live.yml")},
        {"vars": {"wsl_move_rehearsal_live": registered(changed), "wsl_move_rehearsal_baseline": registered(read_base)},
         "tasks": checks_of("live.yml")},
        # A check run before the setup: no baseline file, so the read is its own baseline.
        {"vars": {"wsl_move_rehearsal_live": registered(live_now), "wsl_move_rehearsal_baseline": registered([None])},
         "tasks": checks_of("live.yml")},
    ]
    ok, more, live_ok, live_changed, first = run_plays(tmp_path, plays)
    assert not passed(ok) and failed_by_assert(more, "failed_when of No VM")
    assert not passed(live_ok) and failed_by_assert(live_changed, "The live parts are as at the baseline")
    assert not passed(first)


@needs_both
def test_after_the_restart_the_baseline_keeps_its_parts_and_the_poll_compares_the_count(tmp_path):
    _, baseline = live_and_baseline(tmp_path, VMS)
    before = json.loads(baseline.read_text())
    marked = run_script(tmp_path, "restart.yml", "Mark the baseline", {"Path": str(baseline)})
    assert marked == []
    after = json.loads(baseline.read_text())
    assert after == {**before, "restarted": True}
    # A second mark changes nothing.
    run_script(tmp_path, "restart.yml", "Mark the baseline", {"Path": str(baseline)})
    assert json.loads(baseline.read_text()) == after
    new_vms = [(6000, "vmmemWSL", "2026-03-05T00:00:00.0000000Z"), (6001, "vmmem", "2026-03-05T00:00:01.0000000Z")]
    poll = run_script(tmp_path, "wayback.yml", "No VM of the rehearsal", {"Path": str(baseline)},
                      stand_ins=LIVE_STAND_INS + cim_stand_in(new_vms))
    assert poll[-1]["by"] == "count" and poll[-1]["same"] is True
    # A process with no start time reads with an empty one, and the read goes on.
    no_time = run_script(tmp_path, "live.yml", "Read the live parts", {"Script": str(LIVE), "PresentUser": "nobody"},
                         stand_ins=LIVE_STAND_INS + cim_stand_in([(7, "vmmem", None)]))
    assert no_time[-1]["vm"] == ["@{id=7; name=vmmem; created=}"]
    read_base = run_script(tmp_path, "live.yml", "Read the baseline", {"Path": str(baseline)})
    live_new = run_script(tmp_path, "live.yml", "Read the live parts", {"Script": str(LIVE), "PresentUser": "nobody"},
                          stand_ins=LIVE_STAND_INS + cim_stand_in(new_vms))
    [result] = run_plays(tmp_path, [{"vars": {"wsl_move_rehearsal_live": registered(live_new),
                                              "wsl_move_rehearsal_baseline": registered(read_base)},
                                     "tasks": checks_of("live.yml")}])
    assert not passed(result), passed(result)


TOKENS_STAND_INS = r"""
class TnwksToken { static [string[]] EnabledGroups([int]$ProcessId) { return @('S-1-5-32-545', 'S-1-16-8192') } }
function Invoke-CimMethod { param($InputObject, [string]$MethodName) [pscustomobject]@{ Sid = $InputObject.Owner } }
"""


@needs_both
def test_the_wayback_checks_pass_on_the_reads_after_a_way_back(tmp_path):
    request = {"mode": "wayback", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True}
    state = "t window-started\nt window-done\n"
    run = run_window(tmp_path / "w", request, state=state, disk=("b",), acl_file=OLD_SDDL, marker_a=True,
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"},
                     disabled="tnwks-moverh-a-start")
    assert run["code"] == 0, run["log"]
    procedure = procedure_output(tmp_path, run, "2026-01-01T00:00:00.0000000Z forward result: done\r\n", state, request,
                                 ["window-done"])
    # One process of A, with no Administrators group enabled.
    tokens = run_script(tmp_path, "tokens.yml", "Read the tokens", {"Sid": A_SID}, stand_ins=TOKENS_STAND_INS + (
        f"function Get-CimInstance {{ param([string]$ClassName) @([pscustomobject]@{{ ProcessId = 1; Name = 'wsl.exe'; Owner = '{A_SID}' }}) }}\n"))
    assert tokens[-1] == {"processes": 1, "admin_enabled": 0, "errors": []}
    disk = tmp_path / "a.vhdx"
    disk.write_text("disk")
    back = run_script(tmp_path, "wayback.yml", "Read the tasks and the file", {
        "Prefix": "tnwks-moverh", "Disk": str(disk), "DiskB": str(tmp_path / "none.vhdx")},
        stand_ins=TASKS + "$global:TaskStates = @{ 'tnwks-moverh-a-start' = 'Running'; 'tnwks-moverh-b-start' = 'Disabled' }\n")
    # The start task of A started a VM since the restore: its rule is allowed.
    rules = disk_read(tmp_path, "saved", [OLD_SDDL + VM_ACE], saved=OLD_SDDL)
    assert rules[-1]["rules_ok"] is True
    check_a = runner_output(tmp_path, "a", "check", check_result(f"{DIR}\\a\\ext4.vhdx"))
    check_b = runner_output(tmp_path, "b", "check", {"registered": False, "oobe_complete": 1})
    strip = runner_output(tmp_path, "b", "strip-rules", {"owner_self": True, "system_or_admins": 0, "rules": 1})
    kept_rules = runner_output(tmp_path, "b", "strip-rules", {"owner_self": True, "system_or_admins": 2, "rules": 3})
    facts = {
        "wsl_move_rehearsal_procedure_run": registered(procedure), "wsl_move_rehearsal_request": request,
        "wsl_move_rehearsal_tokens": registered(tokens), "wsl_move_rehearsal_back_read": registered(back),
        "wsl_move_rehearsal_disk": registered(rules),
        "wsl_move_rehearsal_runs": {"check": [check_a[-1], check_b[-1]], "strip-rules": [strip[-1]]},
        "wsl_move_rehearsal_a_started": registered([{"running": True, "wsl": 1}]),
    }
    names = ("Only B has a rule", "No WSL process of A", "A owns the distro again", "The start task of A")
    kept = {**facts, "wsl_move_rehearsal_runs": {**facts["wsl_move_rehearsal_runs"], "strip-rules": [kept_rules[-1]]}}
    result, not_stripped = run_plays(tmp_path, [{"vars": facts, "tasks": checks_of("run_procedure.yml") + checks_of("wayback.yml", names)},
                                                {"vars": kept, "tasks": checks_of("wayback.yml", names[:1])}])
    assert not passed(result), passed(result)
    assert failed_by_assert(not_stripped, names[0])


@needs_both
def test_a_log_text_over_two_lines_fails_a_check_and_breaks_no_template(tmp_path):
    request = {"mode": "wayback", "group_steps": True, "stop_after": 0, "rehearsal_group_test": True}
    run = run_window(tmp_path / "w", request, state="t window-started\nt window-done\n", disk=("b",), acl_file=OLD_SDDL,
                     marker_a=True, disabled="tnwks-moverh-a-start",
                     tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Ready"})
    root = Path(run["root"])
    log = root / "state/window.log"
    log.write_text(log.read_text() + "2026-01-01T00:00:00.0000000Z wayback fail: one\r\ntwo\r\n")
    procedure = procedure_output(tmp_path, run, "", "t window-started\nt window-done\n", request, "window-done")
    assert "" in procedure[-1]["log"] and None not in procedure[-1]["log"]
    facts = {"wsl_move_rehearsal_procedure_run": registered(procedure), "wsl_move_rehearsal_request": request,
             "wsl_move_rehearsal_runs": {"check": [{"result": {"registered": False}}, {"result": {"registered": False}}]},
             "wsl_move_rehearsal_back_read": registered([{}]), "wsl_move_rehearsal_disk": registered([{}])}
    # A log of the play with a null entry, as a log read before the guard gave.
    null_log = {**facts, "wsl_move_rehearsal_procedure_run": registered([{**procedure[-1], "log": [*procedure[-1]["log"], None]}])}
    for result in run_plays(tmp_path, [{"vars": v, "tasks": checks_of("run_procedure.yml") + checks_of(
            "wayback.yml", ("A owns the distro again",))} for v in (facts, null_log)]):
        assert failed_by_assert(result, "A owns the distro again"), result


# --- Interrupt: a log of one line before the run ------------------------------------------

@needs_both
def test_the_interrupt_checks_pass_on_a_run_after_a_log_of_one_line(tmp_path):
    request = {"mode": "forward", "group_steps": False, "stop_after": 5, "rehearsal_group_test": False}
    state = "t window-started\nt wayback-done\n"
    run = run_window(tmp_path / "w", request, state=state, disk=("a",), saved_version="none")
    assert run["code"] == 0, run["log"]
    procedure = procedure_output(tmp_path, run, "2026-01-01T00:00:00.0000000Z boot result: no-action\r\n", state, request,
                                 ["wayback-done"])
    # One new marker: a list of one, not a text.
    assert procedure[-1]["markers"] == ["window-started"] and procedure[-1]["groups"] == []
    check_a = runner_output(tmp_path, "a", "check", {"registered": False, "oobe_complete": 1})
    facts = {"wsl_move_rehearsal_procedure_run": registered(procedure), "wsl_move_rehearsal_request": request,
             "wsl_move_rehearsal_runs": {"check": [check_a[-1]]}}
    [result] = run_plays(tmp_path, [{"vars": facts, "tasks": checks_of("run_procedure.yml") + checks_of("interrupt.yml")}])
    assert not passed(result), passed(result)
    # Out of order: the run does not start.
    error = run_script(tmp_path, "run_procedure.yml", "The window procedure", {
        "Task": "t", "Request": str(tmp_path / "r.json"), "RequestJson": "{}", "Log": str(tmp_path / "x.log"),
        "State": str(Path(run["root"]) / "state/window.state"), "LastMarkers": ["wayback-done"]},
        stand_ins=TASKS, fails=True)
    assert "The last marker is 'window-started', not one of 'wayback-done': the phase runs out of order" in error


# --- Restart, after-boot, removal: the boot runs in the log -------------------------------

BOOT_STAND_INS = r"""
function Get-CimInstance { param([string]$ClassName) [pscustomobject]@{ LastBootUpTime = $global:Boot } }
"""


def boot_log(tmp_path: Path) -> tuple[Path, Path, str]:
    """A log and a state file after the first restart: a line of an earlier boot, then
    the real run of the boot task (the way back once)."""
    run = run_window(tmp_path / "boot", mode="boot", state="t window-started\n", disk=("b",), acl_file=OLD_SDDL,
                     marker_a=True, tasks={"tnwks-moverh-a-start": "Disabled", "tnwks-moverh-b-start": "Running"},
                     disabled="tnwks-moverh-a-start")
    assert run["code"] == 0, run["log"]
    root = Path(run["root"])
    log = tmp_path / "window.log"
    # A text that went over two lines: its second line has no time.
    log.write_text("2026-01-01T00:00:00.0000000Z boot start, request: {}\r\n2026-01-01T00:00:01.0000000Z boot fail: one\r\n"
                   "two boot lines\r\n2026-01-01T00:00:02.0000000Z boot result: failed\r\n" + (root / "state/window.log").read_text())
    state = tmp_path / "window.state"
    state.write_text((root / "state/window.state").read_text())
    return log, state, "$global:Boot = (Get-Date).AddHours(-1)\n"


def hand_run(tmp_path: Path, log: Path, state: Path) -> None:
    """The boot task started by hand: the run of the procedure appends its lines."""
    again = run_window(tmp_path / "hand", mode="boot", state=state.read_text(), disk=("a",))
    assert again["code"] == 0, again["log"]
    log.write_text(log.read_text() + (Path(again["root"]) / "state/window.log").read_text())


@needs_both
def test_the_after_boot_checks_read_each_run_as_a_list_of_lines(tmp_path):
    log, state, boot = boot_log(tmp_path)
    params = {"Log": str(log), "State": str(state)}
    first = run_script(tmp_path, "after-boot.yml", "Read the log of the boot runs and the markers", params,
                       stand_ins=BOOT_STAND_INS + boot)
    # One run of this boot: a list of one list of lines.
    assert len(first[-1]["runs"]) == 1 and isinstance(first[-1]["runs"][0], list), first
    assert first[-1]["results"] == ["result: done"]
    hand_run(tmp_path, log, state)
    second = run_script(tmp_path, "after-boot.yml", "Read the log of the boot runs again", params,
                        stand_ins=BOOT_STAND_INS + boot)
    assert len(second[-1]["runs"]) == 2
    check_a = runner_output(tmp_path, "a", "check", check_result(f"{DIR}\\a\\ext4.vhdx"))
    check_b = runner_output(tmp_path, "b", "check", {"registered": False, "oobe_complete": 1})
    hand = [{"task": "tnwks-moverh-boot", "ran": True, "code": 0, "hex": "0x00000000", "refused": "", "result": None}]
    # The saved rules, and a stale rule of a VM from before the restart.
    disk = disk_read(tmp_path, "saved", [OLD_SDDL + VM_ACE + VM_ACE.replace("4444", "5555")], saved=OLD_SDDL)
    facts = {"wsl_move_rehearsal_boot_log": registered(first), "wsl_move_rehearsal_boot_log2": registered(second),
             "wsl_move_rehearsal_boot_hand": registered(hand), "wsl_move_rehearsal_disk": registered(disk),
             "wsl_move_rehearsal_runs": {"check": [check_a[-1], check_b[-1]]}}
    # With the default of two levels, each run would be one text.
    flat = {**facts,
            "wsl_move_rehearsal_boot_log": registered(run_script(
                tmp_path, "after-boot.yml", "Read the log of the boot runs and the markers", params,
                stand_ins=BOOT_STAND_INS + boot, depth=2)),
            "wsl_move_rehearsal_boot_log2": registered(run_script(
                tmp_path, "after-boot.yml", "Read the log of the boot runs again", params,
                stand_ins=BOOT_STAND_INS + boot, depth=2))}
    assert isinstance(flat["wsl_move_rehearsal_boot_log2"]["output"][-1]["runs"][0], str)
    ok, two_levels = run_plays(tmp_path, [{"vars": facts, "tasks": checks_of("after-boot.yml")},
                                          {"vars": flat, "tasks": checks_of("after-boot.yml")}])
    assert not passed(ok), passed(ok)
    assert failed_by_assert(two_levels, "The run by hand did nothing")


@needs_pwsh
def test_each_wait_for_a_task_waits_through_queued_and_0x41301(tmp_path):
    """A task that never ran can read a new run time while it is Queued, or Ready with the
    result 0x41301: the runner, the run of the procedure and the wait for the boot task
    wait for the real end (the procedure's own wait: test_wsl_move_rehearsal.py)."""
    queued = "$global:QueuedPolls = 2\n"
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "check.json").write_text(json.dumps({"step": "check", "time": 4102444800}))
    runner = run_script(tmp_path, "run_task.yml", "Run the task", {
        "Runner": str(RUNNER), "Task": "tnwks-moverh-a-check", "Result": str(log_dir / "check.json"), "Minutes": "20",
    }, stand_ins=TASKS + queued)
    assert runner[-1]["code"] == 0 and runner[-1]["ran"] and runner[-1]["result"], runner
    files = tmp_path / "proc"
    files.mkdir()
    for name, text in (("window.log", ""), ("window.state", ""), ("run.log", "2026-01-01T00:00:00.0000000Z forward result: done\r\n"),
                       ("run.state", "2026-01-01T00:00:00Z window-started\r\n2026-01-01T00:00:01Z window-done\r\n")):
        (files / name).write_text(text)
    procedure = run_script(tmp_path, "run_procedure.yml", "The window procedure", {
        "Task": "tnwks-moverh-window", "Request": str(files / "request.json"), "RequestJson": "{}",
        "Log": str(files / "window.log"), "State": str(files / "window.state"), "LastMarkers": [""],
    }, stand_ins=PROCEDURE_STAND_INS + queued + f"""
$global:LogPath = {ps_str(files / 'window.log')}; $global:StatePath = {ps_str(files / 'window.state')}
$global:RunLog = {ps_str(files / 'run.log')}; $global:RunState = {ps_str(files / 'run.state')}
""")
    assert procedure[-1]["code"] == 0 and procedure[-1]["ran"] and procedure[-1]["result"] == "done", procedure
    boot = run_script(tmp_path, "restart.yml", "Wait for the end", {"Task": "tnwks-moverh-boot"},
                      stand_ins=TASKS + BOOT_STAND_INS + "$global:Boot = [datetime]'2025-12-31'\n$global:PendingNow = 2\n")
    assert boot == [{"done": True, "code": 0}]


@needs_both
def test_the_restart_and_removal_checks_read_the_marker_and_the_run_of_this_boot(tmp_path):
    log, state, boot = boot_log(tmp_path)
    marker = run_script(tmp_path, "restart.yml", "Read the last marker", {"State": str(state)})
    empty = tmp_path / "empty.state"
    empty.write_text("")
    no_marker = run_script(tmp_path, "restart.yml", "Read the last marker", {"State": str(empty)})
    assert marker == ["wayback-done"] and no_marker == [""]
    wait = run_script(tmp_path, "restart.yml", "Wait for the end", {"Task": "tnwks-moverh-boot"},
                      stand_ins=TASKS + BOOT_STAND_INS + "$global:Boot = [datetime]'2025-12-31'\n")
    # Task Scheduler that does not answer at first.
    late = run_script(tmp_path, "restart.yml", "Wait for the end", {"Task": "tnwks-moverh-boot"},
                      stand_ins=TASKS + BOOT_STAND_INS + r"""
$global:Boot = [datetime]'2025-12-31'
$global:Calls = 0
function Get-ScheduledTaskInfo { param([string]$TaskName) $global:Calls++; if ($global:Calls -lt 3) { throw 'not yet' }
    [pscustomobject]@{ LastRunTime = [datetime]'2026-01-01'; LastTaskResult = 0 } }
""")
    assert wait == [{"done": True, "code": 0}] and late == [{"done": True, "code": 0}]
    hand_run(tmp_path, log, state)
    now = run_script(tmp_path, "remove.yml", "Read the run of the boot task at this boot", {"Log": str(log)},
                     stand_ins=BOOT_STAND_INS + boot)
    # A removal after the second restart: only the hand run of the boot task is at this
    # boot here, and it found the marker of a done way back.
    plays = [
        {"vars": {"wsl_move_rehearsal_restart_marker": registered(marker), "wsl_move_rehearsal_restart_go": True,
                  "wsl_move_rehearsal_boot_wait": registered(wait)}, "tasks": checks_of("restart.yml")},
        {"vars": {"wsl_move_rehearsal_restart_marker": registered(no_marker), "wsl_move_rehearsal_restart_go": True,
                  "wsl_move_rehearsal_boot_wait": registered(wait)}, "tasks": checks_of("restart.yml")},
        {"vars": {"wsl_move_rehearsal_boot_now": registered(now)}, "tasks": checks_of("remove.yml")},
        {"vars": {"wsl_move_rehearsal_boot_now": registered([""])}, "tasks": checks_of("remove.yml")},
        {"vars": {"wsl_move_rehearsal_last_marker": registered(marker)},
         "tasks": [{"name": "the tasks to remove", "ansible.builtin.set_fact": {
             "tnwks_removed": find_task("remove-before-restart.yml", "No task of the rehearsal, but the boot task")["loop"]}}],
         "keep": ["tnwks_removed"]},
        {"vars": {"wsl_move_rehearsal_last_marker": registered(["window-started"])},
         "tasks": [{"name": "the tasks to remove", "ansible.builtin.set_fact": {
             "tnwks_removed": find_task("remove-before-restart.yml", "No task of the rehearsal, but the boot task")["loop"]}}],
         "keep": ["tnwks_removed"]},
    ]
    restart_ok, restart_none, remove_ok, remove_none, keeps_boot, removes_boot = run_plays(tmp_path, plays)
    assert not passed(restart_ok), passed(restart_ok)
    assert failed_by_assert(restart_none, "The restart follows")
    assert not passed(remove_ok), (now, passed(remove_ok))
    assert failed_by_assert(remove_none, "At this boot")
    assert "tnwks-moverh-boot" not in keeps_boot["facts"]["tnwks_removed"]
    assert "tnwks-moverh-window" in keeps_boot["facts"]["tnwks_removed"]
    assert "tnwks-moverh-boot" in removes_boot["facts"]["tnwks_removed"]


# --- Each path that the play gives a script -----------------------------------------------

# The registered values and the vars of include_tasks that the parameters name.
PARAMETER_VARS = {
    "wsl_move_rehearsal_sids": {"a": A_SID, "b": B_SID}, "windows_desktop_user": "person",
    "wsl_move_rehearsal_side_to": "side", "wsl_move_rehearsal_run": {"key": "a", "step": "check"},
    "wsl_move_rehearsal_request": {"mode": "forward"}, "wsl_move_rehearsal_last_markers": ["", "wayback-done"],
    "wsl_move_rehearsal_disk_read": {"key": "b", "rule": "moved"},
    "wsl_move_rehearsal_live": {"output": [{"vm": []}]}, "wsl_move_rehearsal_removed_sids": A_SID,
}


@no_ansible
def test_the_play_gives_each_script_as_written_and_no_path_with_a_doubled_backslash(tmp_path):
    # In a template, a backslash inside a Jinja string stays as written: '\\' gives two.
    tasks, names, scripts = [], [], []
    for file in sorted(p.name for p in (ROLE / "tasks").glob("*.yml")):
        for task in tasks_in(file):
            module = task.get("ansible.windows.win_powershell")
            if not module or not module.get("parameters"):
                continue
            task_vars = {k: v for k, v in (task.get("vars") or {}).items() if not k.startswith("ansible_")}
            names.append(f"{file}: {task.get('name')}")
            loop = {k: task[k] for k in ("loop", "loop_control") if k in task}
            tasks.append({"name": names[-1], **({"vars": task_vars} if task_vars else {}), **loop,
                          "ansible.builtin.set_fact": {f"tnwks_p{len(tasks)}": module["parameters"],
                                                       f"tnwks_s{len(tasks)}": module["script"]}})
            scripts.append(module["script"])
    [result] = run_plays(tmp_path, [{"vars": PARAMETER_VARS, "tasks": tasks,
                                     "keep": [f"tnwks_{k}{i}" for i in range(len(tasks)) for k in "ps"]}])
    assert not passed(result), passed(result)
    # The play templates each script too: a script goes to the host as written, with the
    # functions of the procedure in the place of its one variable.
    lib = ("{{ wsl_move_rehearsal_procedure_lib }}", VARS["wsl_move_rehearsal_procedure_lib"])
    changed = [names[i] for i in range(len(tasks)) if result["facts"][f"tnwks_s{i}"] != scripts[i].replace(*lib)]
    assert changed == []

    def texts(value):
        # A parameter of JSON text (a config) counts by its values.
        if isinstance(value, str) and value.startswith("{"):
            value = json.loads(value)
        if isinstance(value, dict):
            return [t for v in value.values() for t in texts(v)]
        if isinstance(value, list):
            return [t for v in value for t in texts(v)]
        return [value] if isinstance(value, str) else []

    doubled = [f"{names[i]}: {text}" for i in range(len(tasks))
               for text in texts(result["facts"][f"tnwks_p{i}"]) if "\\\\" in text]
    assert doubled == []
    stops = result["facts"][f"tnwks_p{names.index('procedure_stops.yml: The stops of the procedure on a test config')}"]
    test_config = json.loads(stops["TestConfig"])
    assert test_config["state_file"] == DIR + "\\test\\window.state"
    assert test_config["present"]["folder"] == DIR + "\\test\\a" and test_config["present"]["account"] == "tnwks-moverh-a"
    move = result["facts"][f"tnwks_p{names.index('move_side.yml: Move the disk file of B to the {{ wsl_move_rehearsal_side_to }}')}"]
    assert move["From"] == DIR + "\\b\\ext4.vhdx"


# --- Setup and the check play: the reads of empty files -----------------------------------

@needs_both
def test_the_reads_of_an_empty_state_file_and_an_empty_or_short_log_pass_their_checks(tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("")
    markers = run_script(tmp_path, "setup.yml", "Read the markers of the state file", {"State": str(empty)})
    assert markers == [0]
    one = tmp_path / "one.log"
    one.write_text("2026-01-01T00:00:00.0000000Z forward start, request: "
                   '{"mode":"forward","group_steps":true,"stop_after":0,"rehearsal_group_test":true}\r\n')
    reads = {}
    for name, path in (("empty", empty), ("one", one), ("none", tmp_path / "missing.log")):
        reads[name] = run_script(tmp_path, "check.yml", "Read the moments in Administrators", {"Log": str(path)})
        assert reads[name][-1]["problems"] == [], reads[name]
    groups = run_script(tmp_path, "groups.yml", "The accounts of the rehearsal are in Users only",
                        {"Names": ["tnwks-moverh-a", "tnwks-moverh-b"]}, stand_ins=r"""
function Get-LocalUser { param([string]$Name) [pscustomobject]@{ SID = [pscustomobject]@{ Value = "S-1-5-21-1-1-1-$($Name.Length)" } } }
function Get-LocalGroup { @([pscustomobject]@{ Name = 'Users'; SID = [pscustomobject]@{ Value = 'S-1-5-32-545' } },
    [pscustomobject]@{ Name = 'Administrators'; SID = [pscustomobject]@{ Value = 'S-1-5-32-544' } }) }
function Get-LocalGroupMember { param([string]$SID) @() }
""")
    assert groups == []
    plays = [{"vars": {"wsl_move_rehearsal_setup_markers": registered(markers)},
              "tasks": checks_of("setup.yml", ("The state file holds no marker",))},
             {"vars": {"wsl_move_rehearsal_groups": registered(groups)}, "tasks": checks_of("groups.yml")}]
    plays += [{"vars": {"wsl_move_rehearsal_group_log": registered(r)}, "tasks": checks_of("check.yml", ("Each moment",))}
              for r in reads.values()]
    for result in run_plays(tmp_path, plays):
        assert not passed(result), passed(result)


# --- The copies of the scripts and the configs on the host ---------------------------------

def includes_of(file: str) -> list[tuple[str, str]]:
    """Each include of host_files.yml in a task file, with its mode, and the names of the
    tasks in order."""
    return [(t["name"], t["vars"]["wsl_move_rehearsal_host_files"]) for t in tasks_in(file)
            if t.get("ansible.builtin.include_tasks") == "host_files.yml"]


def test_each_phase_takes_the_copies_of_the_role_as_its_state_allows():
    """Setup, window and interrupt refresh the copies (each starts with no started
    window); wayback, restart and after-boot only compare them, as a window started with
    the copies that are there; the removal prints them. Each comes before the first
    script of the phase that reads with them."""
    modes = {"setup.yml": "refresh", "window.yml": "refresh", "interrupt.yml": "refresh", "wayback.yml": "assert",
             "restart.yml": "assert", "after-boot.yml": "assert", "remove-before-restart.yml": "print"}
    for file, mode in modes.items():
        assert [m for _, m in includes_of(file)] == [mode], file
    assert includes_of("remove.yml") == []
    window = [t.get("name", "") for t in tasks_in("window.yml")]
    at = window.index(includes_of("window.yml")[0][0])
    first_read = next(i for i, t in enumerate(tasks_in("window.yml")) if t.get("ansible.builtin.include_tasks") == "read_disk.yml")
    assert at < first_read < window.index("Read when the password of B was last set")
    setup = [t.get("name", "") for t in tasks_in("setup.yml")]
    assert setup.index(includes_of("setup.yml")[0][0]) < setup.index("A imports the distro into its folder")
    restart = [t.get("name", "") for t in tasks_in("restart.yml")]
    assert restart.index(includes_of("restart.yml")[0][0]) < restart.index("Restart the host")
    # One task file writes the scripts, the configs and the tasks; setup has no copy of its own.
    for file in modes:
        text = (ROLE / "tasks" / file).read_text()
        assert "Invoke-TnwksMoveWindow.ps1\n" not in text and "to_nice_json" not in text, file
    host = (ROLE / "tasks/host_files.yml").read_text()
    assert "loop: \"{{ wsl_move_rehearsal_scripts }}\"" in host
    # The read of the live parts is one of the scripts, with its SHA-256. It only reads:
    # live.yml copies it before each read, at any marker, and setup has no copy of it.
    names = yaml.safe_load((ROLE / "vars/main.yml").read_text())["wsl_move_rehearsal_scripts"]
    assert sorted(names) == sorted(p.name for p in (ROLE / "files").glob("*.ps1")), names
    assert "Get-TnwksMoveLiveState.ps1" not in (ROLE / "tasks/setup.yml").read_text()
    live = [t.get("name") for t in tasks_in("live.yml")]
    copy = next(t for t in tasks_in("live.yml") if "ansible.windows.win_copy" in t)
    assert copy["ansible.windows.win_copy"]["src"] == "Get-TnwksMoveLiveState.ps1"
    assert copy["when"] == "wsl_move_rehearsal_live_dir.stat.exists"
    assert live.index("The folder of the rehearsal is on the host") < live.index(copy["name"]) < live.index("Read the live parts")
    assert "ansible_check_mode and path | win_basename == 'Get-TnwksMoveLiveState.ps1'" in host
    assert "Content: \"{{ item.value | to_nice_json | b64encode }}\"" in host
    hashes = (ROLE / "vars/main.yml").read_text()
    assert "lookup('ansible.builtin.file', name, rstrip=false) | hash('sha256')" in hashes
    assert "value | to_nice_json | hash('sha256')" in hashes


@needs_both
def test_the_copies_change_only_with_no_started_window_and_a_copy_that_differs_stops_the_phase(tmp_path):
    role_hashes = {r"C:\r\Invoke-TnwksMoveWindow.ps1": "a" * 64, r"C:\r\window.json": "b" * 64}
    states = {"": True, "wayback-done": True, "window-started": False, "window-done": False, "boot-run": False}
    plays = []
    for marker in states:
        state = tmp_path / f"state-{marker or 'none'}"
        state.write_text(f"t {marker}\r\n" if marker else "")
        read = run_script(tmp_path, "host_files.yml", "Read the last marker before the copies", {"State": str(state)})
        for mode in ("refresh", "assert"):
            plays.append({"vars": {"wsl_move_rehearsal_files_marker": registered(read), "wsl_move_rehearsal_host_files": mode,
                                   "wsl_move_rehearsal_file_hashes": role_hashes,
                                   "wsl_move_rehearsal_host_hashes": registered([role_hashes])},
                          "tasks": checks_of("host_files.yml")})
    results = run_plays(tmp_path, plays)
    for i, (marker, ok) in enumerate(states.items()):
        refresh, compare = results[2 * i], results[2 * i + 1]
        if ok:
            assert not passed(refresh), (marker, passed(refresh))
        else:
            assert failed_by_assert(refresh, "A refresh of the copies only when no window is started"), marker
        assert not passed(compare), (marker, passed(compare))
    # A copy that differs, or none: the phase stops and names it.
    differ = {r"C:\r\Invoke-TnwksMoveWindow.ps1": "c" * 64, r"C:\r\window.json": "none"}
    result = run_plays(tmp_path, [{"vars": {"wsl_move_rehearsal_files_marker": registered(["window-done"]),
                                            "wsl_move_rehearsal_host_files": "assert",
                                            "wsl_move_rehearsal_file_hashes": role_hashes,
                                            "wsl_move_rehearsal_host_hashes": registered([differ])},
                                   "tasks": checks_of("host_files.yml")}])[0]
    assert failed_by_assert(result, "Each copy on the host is the role's")
    msg = next(r["msg"] for r in result["results"] if r["name"] == "Each copy on the host is the role's")
    assert "Invoke-TnwksMoveWindow.ps1, window.json" in msg and "A window started with them" in msg
    assert "end that window with the release that started it" in msg


@needs_pwsh
def test_a_run_of_the_procedure_starts_only_with_the_copies_of_the_role(tmp_path):
    """run_procedure.yml compares the SHA-256 of each copy on the host before it writes
    the request or starts the task: another copy, or none, stops it with nothing changed."""
    files = tmp_path / "proc"
    files.mkdir()
    (files / "window.state").write_text("")
    (files / "window.log").write_text("")
    params = {"Task": "tnwks-moverh-window", "Request": str(files / "request.json"), "RequestJson": "{}",
              "Log": str(files / "window.log"), "State": str(files / "window.state"), "LastMarkers": [""]}
    stand_ins = PROCEDURE_STAND_INS + "function Start-ScheduledTask { throw 'started' }\n"
    for hashes, word in (([f"{'0' * 64} {WINDOW}"], "is not the role's"), ([f"{sha256(WINDOW.read_text())} {files / 'none.ps1'}"], "none")):
        err = run_script(tmp_path, "run_procedure.yml", "The window procedure", params | {"Hashes": hashes},
                         stand_ins=stand_ins, fails=True)
        assert word in err and "Nothing is changed" in err and "started" not in err, err
        assert not (files / "request.json").exists()
