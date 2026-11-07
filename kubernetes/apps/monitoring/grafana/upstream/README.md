# Reference dashboards

These are the same six Grafana.com revisions previously downloaded at pod
startup. `catalogue.json` records each original URL, revision, UID and SHA256.
Original exports are retained byte-for-byte in `raw/`; their author and source
attribution remain in the exports and linked source pages. Grafana.com's API
does not supply a per-export license field; no additional license is asserted
here. Consult each upstream project's terms when redistributing its material.

`observability/generate.py` verifies the hashes, maps the import-only
`${DS_PROMETHEUS}` input to the existing Prometheus datasource UID, removes the
import wrapper and generates the file-provisioned copies and ConfigMaps. The
existing providers, folders, filenames and dashboard UIDs stay stable. Chart-owned
Kubernetes mixins remain owned by their pinned chart.

To update, download the explicitly selected revision to `raw/`, update its source
metadata/hash, regenerate and run the Grafana provisioning/browser rehearsal.
Pods never download dashboards during startup.
