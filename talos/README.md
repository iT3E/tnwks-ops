# Talos Machine Configs

This directory contains Talos Linux machine configurations for both the local
(Docker-mode) development cluster and the production bare-metal MS-01 cluster.

## Directory Layout

```
talos/
├── local/
│   ├── cluster-config.yaml   # talosctl cluster create configuration
│   └── README.md             # Bootstrap notes for Docker mode
└── prod/
    ├── controlplane.yaml     # MS-01 control plane machineconfig
    ├── worker.yaml           # MS-01 worker machineconfig
    ├── patches/              # Machineconfig patches
    │   ├── cluster-discovery.yaml
    │   └── cni-none.yaml
    └── README.md
```

## Usage

### Local (Docker mode)

```bash
task bootstrap:local
# or manually:
talosctl cluster create --config talos/local/cluster-config.yaml
```

### Prod (Bare metal)

```bash
task bootstrap:prod
# or manually:
talosctl apply-config --nodes <IP> --file talos/prod/controlplane.yaml
```
