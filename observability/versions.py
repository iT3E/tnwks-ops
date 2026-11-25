"""Image versions used by both the generated manifests and offline rehearsals.

Renovate updates these through the `observability/versions.py` custom manager in
.github/renovate.json5. Each name is spelled exactly as in the manifest that
deploys it (docker.io/... included), so Renovate puts the pin here and the
manifest's pin in the same PR. PROMETHEUS is CI-only (promtool and the archive
rehearsal). ALLOY is the image of the Alloy chart's version in
kubernetes/apps/monitoring/alloy/app/helmrelease.yaml, which pins no image:
when Renovate moves the chart, move ALLOY to the chart's appVersion.
"""

GARAGE = "docker.io/dxflrs/garage:v2.5.0@sha256:677e5b758e4425a325b811c425c026bf35a4bf42f7ddbaa773de3f4fd779a5a5"
THANOS = "quay.io/thanos/thanos:v0.42.5@sha256:0a546b98a8dd47735456ab389983675ee363d68127e334937a9fceeea10da5e7"
PROMETHEUS = "prom/prometheus:v3.16.0@sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06"
CADDY = "docker.io/library/caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0"
GRAFANA = "grafana/grafana:13.3.2@sha256:81556409ba48b2b922e0adb69a2511337d8ba7578f11962eefa6149a2d815101"
RENDERER = "grafana/grafana-image-renderer:v5.12.11@sha256:cfa6c8f63e6d626f90208f2880f15bd5facc279daec1b9bb642af07aa850d95e"
ALLOY = "docker.io/grafana/alloy:v1.22.0@sha256:9acc2529bc1edb7f5b2bd801dd8334ebc80c158e3a9e5526ecd7c9a273d43189"
