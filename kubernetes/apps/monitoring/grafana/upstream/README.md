# Reference dashboards

Third-party boards provisioned from git. `catalogue.json` records each source
URL, revision, UID, SHA256 and, where the publisher states one, the license. Original exports are retained byte-for-byte in `raw/`; their author and
source attribution remain in the exports and linked source pages.

| Board | Source | License |
| --- | --- | --- |
| UniFi Poller (clients, USW, UAP) | grafana.com revisions of the unpoller boards | MIT (github.com/unpoller/dashboards) |
| NGINX Ingress controller | ingress-nginx `controller-v1.15.1`, `deploy/grafana/dashboards/nginx.json` | Apache-2.0 |
| Alertmanager / Overview | kube-prometheus-stack 92.17.1 | Apache-2.0 |

A board is chosen for usefulness; a missing license is no reason to drop one.
The MikroTik (13679) and Plausible (22200) boards gave way on 2026-11-07 to the
generated Router tab (Network & Routing) and Analytics tab (Apps & Ingress),
which follow the site filter. The old NGINX board (14314, Angular-era panels)
gave way to ingress-nginx's own board, under the UID `nginx`.

`observability/generate.py` verifies the hashes, maps the import-only
`${DS_PROMETHEUS}` input to the existing Prometheus datasource UID, removes the
import wrapper and generates the file-provisioned copies and ConfigMaps. The
existing providers, folders, filenames and dashboard UIDs stay stable. Chart-owned
Kubernetes mixins remain owned by their pinned chart.

To update, download the explicitly selected revision to `raw/`, update its source
metadata/hash, regenerate and run the Grafana provisioning/browser rehearsal.
Pods never download dashboards during startup.
