"""The check wsl-disk-rules of the Windows host check (Design Doc 0004, section 7, row 27).

The host check reads the owner and the rules of the disk file of the WSL distro as
SYSTEM, from the done switch of the host. It passes only when the owner is the account
that owns the distro, and the rules are protected and name SYSTEM, the Administrators
and that account, with any number of entries of a VM and at most one of the capability
SID of WSL. A missing file and a file that can not be read are each a fail.

The rules run in PowerShell with a fake read of the file (the read itself is Windows
only). The read opens the file with the open of the move procedure, word for word, and
a stand-in test checks what the read asks of Windows.
"""

from __future__ import annotations

import re

import textfile
import yaml
from test_host_check_windows import FAKES, RESULT, ROLE, ROOT, SCRIPT, needs_pwsh, pwsh

WINDOW = ROOT / "infrastructure/ansible/roles/windows_wsl_move_rehearsal/files/Invoke-TnwksMoveWindow.ps1"
CHECK = "wsl-disk-rules"
REASON = "homelab_security_host_wsl_disk_rules_reason"
ACCOUNT = "S-1-5-21-1000-2000-3000-1001"
OTHER = "S-1-5-21-1000-2000-3000-1002"
CAPABILITY = ("S-1-15-3-1024-2268835264-3721307629-241982045-173645152-1490879176-104643441-"
              "2915960892-1612460704")
VM = "S-1-5-83-1-111-222-333-444"
FULL = 0x1F01FF


def entry(sid: str, kind: str = "AccessAllowed", flags: int = 0, mask: int = FULL) -> str:
    return f"[pscustomobject]@{{ Type = '{kind}'; Flags = {flags}; Mask = {mask}; Sid = '{sid}' }}"


GOOD = [entry("S-1-5-18"), entry("S-1-5-32-544"), entry(ACCOUNT)]


def security(entries: list[str], owner: str = ACCOUNT, protected: bool = True, dacl: bool = True) -> str:
    """A fake read of the disk file that gives this owner and these rules."""
    items = ", ".join(entries) or ""
    return (
        "function Get-TnwksWslDiskSecurity([string]$Path) {\n"
        f"    [pscustomobject]@{{ Owner = '{owner}'; Dacl = ${str(dacl).lower()}; "
        f"Protected = ${str(protected).lower()}; Entries = @({items}) }}\n"
        "}\n")


def run(read: str, account: str = f"function Get-TnwksAccountSid([string]$Account) {{ '{ACCOUNT}' }}\n",
        disk: str = r"C:\Users\a\AppData\Local\wsl\d\ext4.vhdx") -> dict:
    """The families of the file of a host where each other check passes, with the
    disk check on (-WslDisk) and the fake read `read`."""
    body = FAKES + account + read + f"""
$measure = Measure-TnwksHostCheck -Now $now -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14 -WslDisk '{disk}' `
    -WslDiskAccount 'adult1' -WslCapabilitySid '{CAPABILITY}'
[Console]::Out.Write((Format-TnwksHostCheck -System 'HOST-A' -Measure $measure -Now $now))
"""
    return textfile.parse(pwsh(body).stdout)


def outcome(families: dict) -> tuple[float | None, set[str]]:
    """The result of the check, and the reasons that the file gives."""
    results = {dict(key)["check"]: value for key, value in textfile.series(families, RESULT).items()}
    reasons = {dict(key)["reason"] for key in textfile.series(families, REASON)}
    return results.get(CHECK), reasons


@needs_pwsh
def test_a_disk_file_with_the_rules_of_the_design_passes_with_the_entries_of_wsl():
    assert outcome(run(security(GOOD))) == (0, set())
    # Any number of VMs, and one entry of the capability SID of WSL.
    vms = [entry(VM), entry("S-1-5-83-1-5-6-7-8"), entry(CAPABILITY)]
    assert outcome(run(security(GOOD + vms))) == (0, set())
    families = run(security(GOOD))
    key = (("check", CHECK), ("severity", "high"), ("source", "host-check"), ("system", "HOST-A"))
    assert textfile.series(families, RESULT)[key] == 0


@needs_pwsh
def test_three_bad_files_and_a_missing_file_each_fail():
    """The test of row 27: an entry for another account, no entry for SYSTEM, a file
    that SYSTEM can not read, and a missing file after the done switch."""
    another = run(security(GOOD + [entry(OTHER)]))
    no_system = run(security(GOOD[1:]))
    denied = run("function Get-TnwksWslDiskSecurity([string]$Path) { "
                 "throw (New-Object System.ComponentModel.Win32Exception 5, 'open') }\n")
    missing = run("function Get-TnwksWslDiskSecurity([string]$Path) { "
                  "throw (New-Object System.ComponentModel.Win32Exception 2, 'open') }\n")
    assert outcome(another) == (1, {"other-entry"})
    assert outcome(no_system) == (1, {"missing-entry"})
    assert outcome(denied) == (1, {"unreadable"})
    assert outcome(missing) == (1, {"missing"})
    no_folder = run("function Get-TnwksWslDiskSecurity([string]$Path) { "
                    "throw (New-Object System.ComponentModel.Win32Exception 3, 'open') }\n")
    assert outcome(no_folder) == (1, {"missing"})
    # A fail of the read is a fail of this check, and costs no other result.
    for families in (denied, missing):
        assert textfile.value(families, "homelab_security_host_check_failed_queries", system="HOST-A") == 0
        assert len(textfile.series(families, RESULT)) == 6


@needs_pwsh
def test_each_other_form_of_the_rules_fails_with_its_reason():
    cases = {
        "another owner": (security(GOOD, owner=OTHER), {"owner"}),
        "the Administrators as owner": (security(GOOD, owner="S-1-5-32-544"), {"owner"}),
        "rules that are not protected": (security(GOOD, protected=False), {"not-protected"}),
        "no DACL": (security([], dacl=False), {"not-protected", "missing-entry"}),
        "a deny entry": (security(GOOD + [entry(OTHER, "AccessDenied")]), {"deny"}),
        "a deny entry of the account": (security(GOOD + [entry(ACCOUNT, "AccessDenied")]), {"deny"}),
        "an object entry": (security(GOOD + [entry(OTHER, "AccessAllowedObject")]), {"other-entry"}),
        "an inherited entry of SYSTEM": (security([entry("S-1-5-18", flags=0x10)] + GOOD[1:]),
                                         {"other-entry", "missing-entry"}),
        "an entry with less than full control": (security(GOOD[:2] + [entry(ACCOUNT, mask=0x120089)]),
                                                 {"other-entry", "missing-entry"}),
        "two entries of the account": (security(GOOD + [entry(ACCOUNT)]), {"other-entry"}),
        "two entries of the capability": (security(GOOD + [entry(CAPABILITY), entry(CAPABILITY)]), {"capability"}),
        "another capability SID": (security(GOOD + [entry("S-1-15-3-1024-1-2-3")]), {"capability"}),
        "a VM SID of another form": (security(GOOD + [entry("S-1-5-83-1-1-2-3")]), {"other-entry"}),
        "a VM entry with a flag": (security(GOOD + [entry(VM, flags=0x10)]), {"other-entry"}),
        "a VM entry with less than full control": (security(GOOD + [entry(VM, mask=0x120089)]), {"other-entry"}),
        "the capability as a deny entry": (security(GOOD + [entry(CAPABILITY, "AccessDenied")]), {"deny"}),
        "no entry for the Administrators": (security([GOOD[0], GOOD[2]]), {"missing-entry"}),
        "a link, a second name or a directory": (
            ("function Get-TnwksWslDiskSecurity([string]$Path) { "
             "throw (New-Object System.InvalidOperationException 'a reparse point') }\n"), {"not-a-plain-file"}),
        "an error that is not of Windows": (
            "function Get-TnwksWslDiskSecurity([string]$Path) { throw 'no type' }\n", {"unreadable"}),
    }
    for case, (read, reasons) in cases.items():
        assert outcome(run(read)) == (1, reasons), case


@needs_pwsh
def test_an_account_with_no_sid_is_a_fail():
    no_sid = "function Get-TnwksAccountSid([string]$Account) { throw 'no such account' }\n"
    assert outcome(run(security(GOOD), account=no_sid)) == (1, {"account"})


@needs_pwsh
def test_no_value_of_the_file_leaves_the_host():
    """A label carries a reason from the fixed list only: never a SID or a text of the file."""
    hostile = 'S-1-5-21-9"} 1\nhomelab_security_check_result{x="y'
    families = run(security(GOOD + [entry(hostile)], owner=hostile))
    assert outcome(families) == (1, {"owner", "other-entry"})
    text = pwsh(FAKES + f"function Get-TnwksAccountSid([string]$Account) {{ '{ACCOUNT}' }}\n" + security(
        GOOD + [entry(hostile)], owner=hostile) + f"""
$measure = Measure-TnwksHostCheck -Now $now -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14 -WslDisk 'C:\\d.vhdx' `
    -WslDiskAccount 'adult1' -WslCapabilitySid '{CAPABILITY}'
[Console]::Out.Write((Format-TnwksHostCheck -System 'HOST-A' -Measure $measure -Now $now))
""").stdout
    assert "S-1-5-21" not in text and "x=" not in text
    script = SCRIPT.read_text()
    block = script[script.index("$script:WslDiskReasons"):script.index("$script:FullControl")]
    listed = set(re.findall(r"'([a-z-]+)'", block))
    used = set(re.findall(r"(?:Add-Reason|return) '([a-z-]+)'", script))
    assert used == listed, (used, listed)


@needs_pwsh
def test_the_check_is_off_with_no_disk_file():
    """Before the done switch the role gives no -WslDisk: no result of the check."""
    body = FAKES + "function Get-TnwksWslDiskSecurity([string]$Path) { throw 'not read' }\n" + """
$measure = Measure-TnwksHostCheck -Now $now -SignatureMaxAgeDays 7 -UpdatesMaxAgeDays 14
[Console]::Out.Write((Format-TnwksHostCheck -System 'HOST-A' -Measure $measure -Now $now))
"""
    families = textfile.parse(pwsh(body).stdout)
    assert outcome(families) == (None, set())


def test_the_task_passes_the_disk_file_only_from_the_done_switch():
    template = (ROLE / "templates/host-check.xml.j2").read_text()
    arguments = re.search(r"<Arguments>(.*)</Arguments>", template)[1]
    on = re.search(r"\{% if host_check_wsl_disk_rules \| bool %\}(.*?)\{% endif %\}", arguments)[1]
    assert on == (' -WslDisk "{{ host_check_wsl_disk | e }}" -WslDiskAccount "{{ host_check_wsl_disk_account | e }}"'
                  ' -WslCapabilitySid "{{ host_check_wsl_capability_sid | e }}"')
    assert arguments.count("-WslDisk ") == 1
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    # From the phase done on a host with the move of the distro on, and from its own switch on another host.
    assert " ".join(defaults["host_check_wsl_disk_rules"].split()) == (
        "{{ (wsl_move_phase | default('off') == 'done') if (wsl_move | default(false) | bool) "
        "else (wsl_start_unelevated_done | default(false) | bool) }}")
    # The disk of the compaction, so both read one file.
    assert defaults["host_check_wsl_disk"].startswith("{{ vhdx_compact_vhdx ")
    assert defaults["host_check_wsl_disk_account"] == "{{ host_check_distro_account }}"
    assert defaults["host_check_wsl_capability_sid"] == CAPABILITY
    rehearsal = yaml.safe_load((ROOT / "infrastructure/ansible/roles/windows_wsl_move_rehearsal/defaults/main.yml")
                               .read_text())
    assert rehearsal["wsl_move_rehearsal_vm_capability_sid"] == CAPABILITY


# --- The read: the open of the move procedure, word for word -------------------------

def disk_read_source() -> str:
    return re.search(r"\$script:DiskReadCode = @'\n(.*?)\n'@", SCRIPT.read_text(), re.DOTALL)[1]


def open_block(source: str) -> str:
    """From the struct FileInformation to the end of Open."""
    return re.search(r"(    \[StructLayout\(LayoutKind\.Sequential\)\]\n    struct FileInformation \{.*?\n"
                     r"        return h;\n    \}\n)", source, re.DOTALL)[1]


def test_the_read_opens_the_file_with_the_open_of_the_move_procedure():
    window = re.search(r"\$TnwksSecuritySource = @'\n(.*?)\n'@", WINDOW.read_text(), re.DOTALL)[1]
    assert open_block(disk_read_source()) == open_block(window)
    source = disk_read_source()
    # One handle, from Open, with READ_CONTROL only.
    assert re.findall(r"SafeFileHandle h = (\w+)\(", source) == ["CreateFile", "Open"]
    assert "using (SafeFileHandle h = Open(path, READ_CONTROL))" in source
    assert "const int OWNER = 0x1, DACL = 0x4, READ_CONTROL = 0x20000;" in source
    assert "GetSecurityInfo(h, 1, OWNER | DACL," in source


def test_the_host_check_enables_no_privilege():
    """Row 27: a file whose rules give SYSTEM no right to read them is a fail."""
    text = SCRIPT.read_text()
    for word in ("AdjustTokenPrivileges", "SeBackupPrivilege", "SeRestorePrivilege", "SeTakeOwnershipPrivilege",
                 "EnablePrivilege"):
        assert word not in text, word


# The calls of Windows that Read makes, each with a stand-in that records what it got.
STANDINS = {
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true, CharSet = CharSet.Unicode\)\]\s*"
    r"static extern SafeFileHandle CreateFile\(string name, int access, int share, IntPtr sa, int disposition, "
    r"int flags, IntPtr template\);": """
    public static string Name; public static int Access, Flags;
    public static uint Attributes = 0x20; public static SafeFileHandle Last; public static bool Reached;
    static SafeFileHandle CreateFile(string name, int access, int share, IntPtr sa, int disposition, int flags, IntPtr template) {
        Name = name; Access = access; Flags = flags;
        Last = new SafeFileHandle(new IntPtr(42), false);
        return Last;
    }""",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true\)\]\s*static extern uint GetFileType\(SafeFileHandle h\);":
        "static uint GetFileType(SafeFileHandle h) { return 1; }",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true\)\]\s*"
    r"static extern bool GetFileInformationByHandle\(SafeFileHandle h, out FileInformation information\);":
        "static bool GetFileInformationByHandle(SafeFileHandle h, out FileInformation information) "
        "{ information = new FileInformation(); information.Attributes = Attributes; information.NumberOfLinks = 1; return true; }",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true, CharSet = CharSet.Unicode\)\]\s*"
    r"static extern uint GetFinalPathNameByHandleW\(SafeFileHandle h, System.Text.StringBuilder path, uint length, "
    r"uint flags\);":
        "static uint GetFinalPathNameByHandleW(SafeFileHandle h, System.Text.StringBuilder path, uint length, uint flags) "
        "{ string f = @\"\\\\?\\\" + Name; path.Append(f); return (uint)f.Length; }",
    r"\[DllImport\(\"advapi32.dll\"\)\]\s*static extern int GetSecurityInfo\(SafeFileHandle h, int type, int info, "
    r"out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr sd\);":
        "static int GetSecurityInfo(SafeFileHandle h, int type, int info, out IntPtr owner, out IntPtr group, "
        "out IntPtr dacl, out IntPtr sacl, out IntPtr sd) { owner = group = dacl = sacl = sd = IntPtr.Zero; "
        "Reached = h == Last && type == 1 && info == 5; return 1234; }",
}


def run_read(attributes: int) -> dict[str, str]:
    text = disk_read_source()
    for pattern, replacement in STANDINS.items():
        text, n = re.subn(pattern, lambda _m, r=replacement: r, text)
        assert n == 1, pattern
    script = f"""
Add-Type -TypeDefinition @'
{text}
'@
$t = [TnwksDiskRead]
$t.GetField('Attributes', 'Public,Static').SetValue($null, [uint32]{attributes})
try {{ [void]$t::Read('C:\\d\\ext4.vhdx'); $end = 'returned' }}
catch {{ $e = $_.Exception; while ($e.InnerException) {{ $e = $e.InnerException }}; $end = $e.GetType().Name + ': ' + $e.Message }}
foreach ($n in 'Name', 'Access', 'Flags', 'Reached') {{ "$n=$($t.GetField($n, 'Public,Static').GetValue($null))" }}
"Closed=$($t.GetField('Last', 'Public,Static').GetValue($null).IsClosed)"
"End=$end"
"""
    return dict(line.split("=", 1) for line in pwsh(script).stdout.splitlines() if "=" in line)


@needs_pwsh
def test_the_read_takes_the_owner_and_the_dacl_on_the_handle_of_open_and_refuses_a_directory():
    got = run_read(0x20)
    assert got["Name"] == r"C:\d\ext4.vhdx"
    assert int(got["Access"]) == 0x20000, "READ_CONTROL only"
    assert int(got["Flags"]) == 0x02000000 | 0x00200000 | 0x00100000 | 0x00010000
    assert got["Reached"] == "True" and got["End"].startswith("Win32Exception"), got
    directory = run_read(0x10)
    assert directory["End"] == r"InvalidOperationException: a directory: C:\d\ext4.vhdx", directory
    assert (directory["Reached"], directory["Closed"]) == ("False", "True"), directory
