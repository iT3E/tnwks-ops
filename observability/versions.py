"""Image versions used by both the generated manifests and offline rehearsals.

Renovate updates these through the `observability/versions.py` custom manager in
.github/renovate.json5. Each name is spelled exactly as in the manifest that
deploys it (docker.io/... included), so Renovate puts the pin here and the
manifest's pin in the same PR. PROMETHEUS is CI-only (promtool and the archive
rehearsal).
"""

GARAGE = "docker.io/dxflrs/garage:v2.5.0@sha256:677e5b758e4425a325b811c425c026bf35a4bf42f7ddbaa773de3f4fd779a5a5"
THANOS = "quay.io/thanos/thanos:v0.42.5@sha256:0a546b98a8dd47735456ab389983675ee363d68127e334937a9fceeea10da5e7"
PROMETHEUS = "prom/prometheus:v3.15.1@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
CADDY = "docker.io/library/caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee"
GRAFANA = "grafana/grafana:13.3.1@sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf"
RENDERER = "grafana/grafana-image-renderer:v5.12.10@sha256:a7970b826f6e477461e02bf5463d92634e9ad27d525e4e50296978a2e592d2d7"
