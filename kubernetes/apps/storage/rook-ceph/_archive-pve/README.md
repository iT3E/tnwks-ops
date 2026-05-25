# Legacy: External PVE Ceph Cluster (Archived)

This directory previously configured Rook-Ceph in **external** mode, connecting
to a Ceph cluster running on the Proxmox VE hypervisor hosts. It is **no longer
in use** — the new prod cluster runs Rook in **in-cluster** mode with OSDs on
the 3x 1TB NVMe drives in the MS-01 nodes.

See `../cluster/` for the active in-cluster configuration.

Archived 2026-05-12 with the multi-cluster restructure.
