# EdgeOS devices

Ubiquiti EdgeOS devices managed from this repo. There is no maintained
Terraform provider for EdgeOS, so each device's configuration is kept as the
`set` commands EdgeOS itself prints, and `tools/edgeos-sync.py` diffs and
applies them over SSH.

| Device | Directory | Address |
| --- | --- | --- |
| EP-R6 (`sce-ep01`) | `ep-r6/` | `10.10.140.140` (VLAN 140, reachable over `wg01`) |

Per device:

| File | |
| --- | --- |
| `config.sh` | Desired config: `show configuration commands` output, with `${name}` placeholders for secrets |
| `secrets.sops.yaml` | Placeholder values plus the SSH login the tool uses (`ssh_*` keys) |
| `device.json` | Address and SSH port |

## Workflow

```bash
task edgeos:plan            # DEVICE=ep-r6 by default
task edgeos:apply
```

- **plan** prints what would be deleted and set (secret values masked), then
  stages exactly those lines in a candidate session on the device and
  discards it, so EdgeOS validates the change without committing anything.
- **apply** stages the same lines, schedules `commit-confirm` (5 minutes) and
  commits; then a new SSH login confirms and saves. If the change cuts the
  device off, the confirm never arrives and the device reboots into its saved,
  pre-change config. It finishes by checking that the device matches the repo.

Edit `config.sh`, open a PR with the `task edgeos:plan` output, merge, then
apply from `main`. Changes made on the device directly show up as drift in the
next plan. To capture a deliberate on-device change, copy the relevant lines
from `show configuration commands` into `config.sh`.
