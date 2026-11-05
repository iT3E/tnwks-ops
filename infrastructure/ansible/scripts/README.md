# Scripts outside the playbooks

Host changes go through the roles. These are the exceptions, things Ansible
can't do from inside WSL:

| Script                | What                                                                                                                                                                                                                         |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invoke-elevated.sh`  | Runs a PowerShell script elevated on the Windows host (one UAC prompt) and returns its output and status. `task wsl-host:sshd` uses it for `roles/windows_sshd/files/Install-TnwksWindowsSshd.ps1`, the step before Ansible can connect. |
| `Move-WslDiskToE.ps1` | One-off: moves the WSL distro's vhdx off `C:` with `wsl --manage --move` ([move-disk-off-c.md](../../../docs/wsl/move-disk-off-c.md)). It shuts WSL down, so it has to run from an elevated Windows shell, not from WSL. |
