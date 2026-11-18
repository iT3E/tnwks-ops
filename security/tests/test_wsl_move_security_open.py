"""The open of a file by SYSTEM in the move procedure (TnwksSecurity.Open).

Each read and write of the rules of a file by SYSTEM opens the file through
`Open` of the C# type in Invoke-TnwksMoveWindow.ps1. These tests compile that
type in PowerShell, with the calls of Windows that `Open` makes replaced by
stand-ins, and check what `Open` asks of Windows and what it refuses.

What a stand-in can not show: that Windows itself opens the entry and not the
target of a link with FILE_FLAG_OPEN_REPARSE_POINT, gives the final path and
the number of names as its documentation says, and holds SYSTEM to
identification for a server behind the path. Those come from Microsoft's
documentation of CreateFileW, GetFileType, GetFileInformationByHandle and
GetFinalPathNameByHandleW, and a run on a Windows host shows them.
"""

from __future__ import annotations

import re

from test_wsl_move_rehearsal import WINDOW
from test_wsl_start_unelevated import needs_pwsh, pwsh

SOURCE = re.search(r"\$TnwksSecuritySource = @'\n(.*?)\n'@", WINDOW.read_text(), re.DOTALL)[1]

# The calls of Windows that Open makes, each with its stand-in, which records what
# it was given. The read and the write of the rules on the handle (GetSecurityInfo,
# SetSecurityInfo) record that they got the handle of Open, and fail with an error
# of their own (1234), so the call ends there.
STANDINS = {
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true, CharSet = CharSet.Unicode\)\]\s*"
    r"static extern SafeFileHandle CreateFile\(string name, int access, int share, IntPtr sa, int disposition, int flags, IntPtr template\);": """
    public static string Name; public static int Access, Share, Disposition, Flags;
    public static uint Type = 1, Attributes = 0x20, Links = 1; public static string Final; public static SafeFileHandle Last;
    static SafeFileHandle CreateFile(string name, int access, int share, IntPtr sa, int disposition, int flags, IntPtr template) {
        Name = name; Access = access; Share = share; Disposition = disposition; Flags = flags;
        Last = new SafeFileHandle(new IntPtr(42), false);
        return Last;
    }""",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true\)\]\s*static extern uint GetFileType\(SafeFileHandle h\);":
        "static uint GetFileType(SafeFileHandle h) { return Type; }",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true\)\]\s*"
    r"static extern bool GetFileInformationByHandle\(SafeFileHandle h, out FileInformation information\);":
        "static bool GetFileInformationByHandle(SafeFileHandle h, out FileInformation information) "
        "{ information = new FileInformation(); information.Attributes = Attributes; information.NumberOfLinks = Links; return true; }",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true, CharSet = CharSet.Unicode\)\]\s*"
    r"static extern uint GetFinalPathNameByHandleW\(SafeFileHandle h, System.Text.StringBuilder path, uint length, uint flags\);":
        "static uint GetFinalPathNameByHandleW(SafeFileHandle h, System.Text.StringBuilder path, uint length, uint flags) "
        "{ string f = Final ?? (@\"\\\\?\\\" + Name); path.Append(f); return (uint)f.Length; }",
    r"\[DllImport\(\"advapi32.dll\", SetLastError = true, CharSet = CharSet.Unicode\)\]\s*"
    r"static extern bool ConvertStringSidToSid\(string text, out IntPtr sid\);":
        "static bool ConvertStringSidToSid(string text, out IntPtr sid) { sid = IntPtr.Zero; return true; }",
    r"\[DllImport\(\"advapi32.dll\"\)\]\s*static extern int GetSecurityInfo\(SafeFileHandle h, int type, int info, "
    r"out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr sd\);":
        "public static bool Reached; static int GetSecurityInfo(SafeFileHandle h, int type, int info, out IntPtr owner, out IntPtr group, "
        "out IntPtr dacl, out IntPtr sacl, out IntPtr sd) { owner = group = dacl = sacl = sd = IntPtr.Zero; Reached = h == Last; return 1234; }",
    r"\[DllImport\(\"advapi32.dll\"\)\]\s*static extern int SetSecurityInfo\(SafeFileHandle h, int type, int info, "
    r"IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl\);":
        "static int SetSecurityInfo(SafeFileHandle h, int type, int info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl) "
        "{ Reached = h == Last; return 1234; }",
    r"\[DllImport\(\"kernel32.dll\", SetLastError = true\)\] static extern IntPtr LocalFree\(IntPtr p\);":
        "static IntPtr LocalFree(IntPtr p) { return IntPtr.Zero; }",
}


def standin_source() -> str:
    text = SOURCE
    for pattern, replacement in STANDINS.items():
        text, n = re.subn(pattern, lambda _m, r=replacement: r, text)
        assert n == 1, pattern
    return text


def run_open(call: str, file_type: int = 1, attributes: int = 0x20, links: int = 1, final: str = "") -> dict[str, str]:
    """One call of the type on a stand-in file; what Open was given, and how the call ended."""
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
{standin_source()}
'@
$t = [TnwksSecurity]
$t.GetField('Type', 'Public,Static').SetValue($null, [uint32]{file_type})
$t.GetField('Attributes', 'Public,Static').SetValue($null, [uint32]{attributes})
$t.GetField('Links', 'Public,Static').SetValue($null, [uint32]{links})
if ('{final}') {{ $t.GetField('Final', 'Public,Static').SetValue($null, '{final}') }}
try {{ {call}; $end = 'returned' }}
catch {{ $e = $_.Exception; while ($e.InnerException) {{ $e = $e.InnerException }}; $end = $e.GetType().Name + ': ' + $e.Message }}
foreach ($n in 'Name', 'Access', 'Share', 'Disposition', 'Flags', 'Reached') {{ "$n=$($t.GetField($n, 'Public,Static').GetValue($null))" }}
$last = $t.GetField('Last', 'Public,Static').GetValue($null)
"Closed=$(if ($last) {{ $last.IsClosed }} else {{ 'none' }})"
"End=$end"
"""
    r = pwsh(script)
    assert r.returncode == 0, r.stderr
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


CALLS = {
    "ReadSddl": (r"[TnwksSecurity]::ReadSddl('C:\t\a.vhdx')", 0x20000),
    "ReadGroup": (r"[TnwksSecurity]::ReadGroup('C:\t\a.vhdx')", 0x20000),
    "SetOwner": (r"[TnwksSecurity]::SetOwner('C:\t\a.vhdx', 'BA')", 0x80000),
}

# CreateFileW: FILE_FLAG_BACKUP_SEMANTICS 0x02000000, FILE_FLAG_OPEN_REPARSE_POINT
# 0x00200000, SECURITY_SQOS_PRESENT 0x00100000, SECURITY_IDENTIFICATION 0x00010000.
FLAGS = 0x02000000 | 0x00200000 | 0x00100000 | 0x00010000


@needs_pwsh
def test_each_open_by_system_takes_the_entry_itself_with_identification_only():
    for name, (call, access) in CALLS.items():
        got = run_open(call)
        # The file passed each check of Open, and the rules were read or
        # written on its handle.
        assert got["Reached"] == "True" and got["End"].startswith("Win32Exception"), (name, got)
        assert got["Name"] == r"C:\t\a.vhdx", name
        assert int(got["Flags"]) == FLAGS, (name, hex(int(got["Flags"])))
        assert int(got["Access"]) == access, name
        # OPEN_EXISTING, and every share mode as before.
        assert (got["Disposition"], got["Share"]) == ("3", "7"), name


@needs_pwsh
def test_open_refuses_a_reparse_point_and_what_is_not_a_file_of_a_disk():
    elsewhere = "the path leads to another file, through a link or a junction above it"
    cases = {
        "a link": ((1, 0x20 | 0x400), "a reparse point"),
        "a junction": ((1, 0x10 | 0x400), "a reparse point"),
        "a pipe": ((3, 0x80), "not a file of a disk (type 3)"),
        "a character device": ((2, 0x80), "not a file of a disk (type 2)"),
        "an unknown type": ((0, 0x80), "not a file of a disk (type 0)"),
        "a second name": ((1, 0x20, 2), "a file with 2 names (a hard link)"),
        "a junction above the file": ((1, 0x20, 1, r"\\?\C:\elsewhere\a.vhdx"), elsewhere),
        "a share": ((1, 0x20, 1, r"\\?\UNC\host\share\a.vhdx"), elsewhere),
        "a final path of no known form": ((1, 0x20, 1, r"C:\t\a.vhdx"), elsewhere),
    }
    for name, (call, _access) in CALLS.items():
        for case, (args, words) in cases.items():
            got = run_open(call, *args)
            assert got["End"].startswith("InvalidOperationException: " + words), (name, case, got)
            assert got["End"].endswith(r"C:\t\a.vhdx"), (name, case, got)
            # The handle of the refused entry is closed, and no rule was read or written.
            assert (got["Closed"], got["Reached"]) == ("True", "False"), (name, case, got)
    # A folder of a disk that is not a reparse point passes, and so does a
    # final path that differs in case only, or by a closing backslash.
    assert run_open(CALLS["ReadSddl"][0], 1, 0x10)["Reached"] == "True"
    assert run_open(CALLS["ReadSddl"][0], 1, 0x20, 1, r"\\?\c:\T\A.VHDX")["Reached"] == "True"
    assert run_open(r"[TnwksSecurity]::ReadSddl('C:\t\')", 1, 0x10, 1, r"\\?\C:\t")["Reached"] == "True"


def test_every_handle_of_the_type_comes_from_open():
    # One call of CreateFile, in Open, with the flags of Open; each method that
    # takes a path opens it through Open.
    assert len(re.findall(r"\bCreateFile\(", SOURCE)) == 2  # the declaration and the call in Open
    assert "CreateFile(path, access, 7, IntPtr.Zero, 3, OPEN_FLAGS, IntPtr.Zero)" in SOURCE
    assert re.search(r"const int OPEN_FLAGS = 0x02000000 \| 0x00200000 \| 0x00100000 \| 0x00010000;", SOURCE)
    handles = re.findall(r"SafeFileHandle h = (\w+)\(", SOURCE)
    assert handles and set(handles) == {"Open", "CreateFile"} and handles.count("CreateFile") == 1, handles
