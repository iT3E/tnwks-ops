# The vendor's manifests of the Wazuh server

`upstream/` is a copy of files of the vendor's repository, byte for byte,
with no change. This repository changes them only through the patches of
`../ks.yaml`.

| | |
| --- | --- |
| Repository | <https://github.com/wazuh/wazuh-kubernetes> |
| Tag | `v5.0.0-rc2` |
| Commit | `ceacd20b73fbe5252cc0af1a5429916c4e980fa9` |
| Licence | GPL-2.0, `upstream/LICENSE` |
| Sums | `upstream.sha256`: the sha256 of each file of `upstream/` |

The files, with their paths in the vendor's repository:

- `LICENSE`
- `wazuh/indexer_stack/wazuh-dashboard/dashboard-deploy.yaml`
- `wazuh/indexer_stack/wazuh-dashboard/dashboard-egress.yaml`
- `wazuh/indexer_stack/wazuh-dashboard/dashboard-pvc.yaml`
- `wazuh/indexer_stack/wazuh-dashboard/dashboard-svc.yaml`
- `wazuh/indexer_stack/wazuh-indexer/cluster/indexer-sts.yaml`
- `wazuh/indexer_stack/wazuh-indexer/indexer-egress.yaml`
- `wazuh/indexer_stack/wazuh-indexer/indexer-ingress.yaml`
- `wazuh/indexer_stack/wazuh-indexer/indexer-svc.yaml`
- `wazuh/wazuh_managers/network-policies/manager-egress.yaml`
- `wazuh/wazuh_managers/network-policies/wazuh-master-ingress.yaml`
- `wazuh/wazuh_managers/wazuh-api-svc.yaml`
- `wazuh/wazuh_managers/wazuh-cluster-svc.yaml`
- `wazuh/wazuh_managers/wazuh-master-sts.yaml`

The files of `wazuh/` that this app leaves out, and why, are in
`../README.md`, "How it is built".

## Checks

- CI: `security/tests/test_wazuh_manifests.py` checks each file of
  `upstream/` against `upstream.sha256`, that the list of files is the one
  above, and that `kustomization.yaml` lists each manifest. It needs no
  network.
- Against the vendor, from the root of this repository (a fresh clone at the
  tag, its commit, and each sum):

  ```bash
  d=$(mktemp -d) && git clone -q -c advice.detachedHead=false --depth 1 --branch v5.0.0-rc2 https://github.com/wazuh/wazuh-kubernetes "$d" && test "$(git -C "$d" rev-parse HEAD)" = ceacd20b73fbe5252cc0af1a5429916c4e980fa9 && (cd "$d" && sha256sum --quiet -c "$OLDPWD/kubernetes/apps/security/wazuh/server/upstream.sha256") && echo same; rm -rf "$d"
  ```

## How to update

Nothing tracks the tag of these files: Renovate reads the images of
`../ks.yaml`, not this copy. To move to a new tag:

1. Clone the vendor's repository at the new tag, and note its commit.
2. Copy the files of the list above over `upstream/`, with the same paths.
   Read the vendor's changelog for a new file under `wazuh/` that the
   server needs, and for one that moves.
3. Write `upstream.sha256` again, from `upstream/`:
   `(cd upstream && find . -type f | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum) > upstream.sha256`.
4. Change the tag and the commit here, in `../ks.yaml`, and in
   `security/tests/test_wazuh_manifests.py` (`TAG`, `COMMIT`), and the
   images of the same tag in `../ks.yaml`.
5. Read the diff of `upstream/` in the pull request: each changed line of
   the vendor is in it. Check that each patch of `../ks.yaml` still applies,
   with the render test (`kustomize` installed).
