# UISP

Ubiquiti UISP (formerly UNMS) running on Kubernetes via the
[bjw-s app-template](https://github.com/bjw-s/helm-charts) chart.

## Status

Active — Ivan moved this back from the temporary VyOS Podman host onto
the cluster as part of the multi-cluster restructure (2026-05-12).

## Notes

- StatefulSet because UISP holds device state in `/config`
- `8 CPU / 4Gi` resource pin matches the legacy footprint
- HTTPS-only — backend protocol annotation set on ingress
- Deployed only on the MS-01 cluster (omitted from the WSL overlay)
- Persistent config volume uses `${STORAGE_CLASS_DEFAULT}`
  (`ceph-block` on MS-01)

## Container image

`nico640/docker-unms:2.2.15` — community-maintained image. Renovate
tracks updates via the existing tag pin.
