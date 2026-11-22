# Scripts outside the playbooks

Host changes go through the roles. These are the exceptions, things Ansible
can't do from inside WSL:

| Script                | What                                                                                                                                                                                                                         |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invoke-elevated.sh`  | Runs a PowerShell script elevated on the Windows host (one UAC prompt) and returns its output and status. When the elevated process does not start (a refused prompt, or a session with no interactive desktop) or gives no exit code, it says so in one line and exits 125, so the task fails. It finds Windows PowerShell itself: `powershell.exe` on `PATH`, else its standard place under the mount of the `C:` drive (from `wslpath`, else `/mnt/c`), for a WSL shell that leaves the Windows folders out of `PATH` (`appendWindowsPath=false`). `INVOKE_ELEVATED_POWERSHELL` sets that place instead. With none, it says so in one line and exits 125 before any prompt. `task wsl-host:sshd` uses it for `roles/windows_sshd/files/Install-TnwksWindowsSshd.ps1`, the step before Ansible can connect. |
| `Move-WslDiskToE.ps1` | One-off: moves the WSL distro's vhdx off `C:` with `wsl --manage --move` ([move-disk-off-c.md](../../../docs/wsl/move-disk-off-c.md)). It shuts WSL down, so it has to run from an elevated Windows shell, not from WSL. |
