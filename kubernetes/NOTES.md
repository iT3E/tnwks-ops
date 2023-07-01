### Managing Flux

`kubectl describe hr -n monitoring grafana` for logs

`flux reconcile ks cluster-apps-grafana -n flux-system` to "reload" from git


`flux get ks -A` get kustomizations (what flux is trying to/has applied)

`flux get hr -A` get helmreleases (what flux has applied)


`kubectl cephcluster -n rook-ceph ceph-pve-external` k8s -> ceph communication



# wipe k8s rook-ceph and restart

```
flux suspend ks 1-core-storage-rook-ceph-operator
flux suspend ks 1-core-storage-rook-ceph-pve-cluster
helm uninstall -n rook-ceph rook-ceph-cluster && true || true
flux delete hr -n rook-ceph rook-ceph-cluster --silent && true || true

        for CRD in $(kubectl get crd -n rook-ceph | awk '/ceph.rook.io/ {print $1}'); do
            kubectl get -n rook-ceph "$CRD" -o name | \
            xargs -I {} kubectl patch -n rook-ceph {} --type merge -p '{"metadata":{"finalizers": []}}' && true || true
        done

kubectl -n rook-ceph patch configmap rook-ceph-mon-endpoints --type merge -p '{"metadata":{"finalizers": []}}' && true || true
kubectl -n rook-ceph patch secrets rook-ceph-mon --type merge -p '{"metadata":{"finalizers": []}}' && true || true
helm uninstall -n rook-ceph rook-ceph && true || true
flux delete hr -n rook-ceph rook-ceph --silent && true || true
kubectl get namespaces rook-ceph && until kubectl delete namespaces rook-ceph; do kubectl get namespaces rook-ceph -o jsonpath="{.status}"; done || true
flux resume ks 1-core-storage-rook-ceph-operator
flux resume ks 1-core-storage-rook-ceph-pve-cluster
```
