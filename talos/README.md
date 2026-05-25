# Talos Machine Configs

Two clusters, both running Talos Linux:

- **WSL cluster** — Talos in Docker on the WSL2 desktop; runs real workloads
  alongside the MS-01 cluster (USB-passthrough hosts for Coral, Z-Wave)
- **MS-01 cluster** — bare-metal Talos on 3x Minisforum MS-01 i9-13900H

## Directory Layout

```
talos/
├── wsl/
│   ├── cluster-config.yaml   # talosctl cluster create configuration
│   └── README.md             # Bootstrap notes for Docker mode
└── ms-01/
    ├── controlplane.yaml     # MS-01 control plane machineconfig
    ├── worker.yaml           # MS-01 worker machineconfig
    ├── patches/              # Machineconfig patches
    │   ├── cluster-discovery.yaml
    │   └── cni-none.yaml
    └── README.md
```

## Usage

### WSL cluster (Docker mode)

```bash
task bootstrap:wsl
# or manually:
talosctl cluster create --config talos/wsl/cluster-config.yaml
```

### MS-01 cluster (bare metal)

```bash
task bootstrap:ms-01
# or manually:
talosctl apply-config --nodes <IP> --file talos/ms-01/controlplane.yaml
```
