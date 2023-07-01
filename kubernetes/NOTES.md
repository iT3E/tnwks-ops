### Managing Flux

`kubectl describe hr -n monitoring grafana` for logs

`flux reconcile ks cluster-apps-grafana -n flux-system` to "reload" from git


`flux get ks -A` get kustomizations (what flux is trying to/has applied)

`flux get hr -A` get helmreleases (what flux has applied)


`kubectl cephcluster -n rook-ceph ceph-pve-external` k8s -> ceph communication

