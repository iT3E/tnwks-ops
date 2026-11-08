"""Apps & Ingress (L2): Can users reach a usable application, and where is the request path failing?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import NGINX, metric, selector


def board() -> Board:
    return Board("tnwks-apps", "Apps & Ingress", "apps", "l2",
              "Can users reach a usable application, and where is the request path failing?", {
            "User paths": [
                p("Application probe state", metric("homelab:service_state"), "state",
                  "Content probe to the in-cluster Service on its active site. This bypasses client DNS, ingress and sign-in; those paths need separate evidence.", "table"),
                p("HTTP requests", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "reqps",
                  "Rate counters before aggregation. Use the same scope when comparing the error numerator and total denominator.", link=NGINX),
                p("HTTP 5xx fraction", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests", "status=~\"5..\"")}[$__rate_interval])) / sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "percentunit",
                  "Weighted errors/requests. No traffic is not a perfect availability measurement; independent synthetic evidence remains necessary.", link=NGINX),
                p("HTTP p95 duration", f'histogram_quantile(0.95, sum by (k8s_cluster, ingress, le) (rate({metric("nginx_ingress_controller_request_duration_seconds_bucket")}[$__rate_interval])))', "s",
                  "Buckets combined before calculating the percentile. Never average per-pod percentiles.", link=NGINX),
            ],
            "Shared-VIP names": [
                p("Name state by site", metric("homelab:ingress_state"), "state",
                  "Each site's collector uses every shared-VIP name as a client would: the router's DNS answer must be the name's VIP, TLS must verify, and that site's ingress must route the host (any answer but a 404 or 5xx). A pod reaches its own site's ingress for either VIP, so this is each site's readiness to serve the name.", "table"),
                p("Served by", f'max by (k8s_cluster, name, served_cluster) ({selector("homelab_ingress_served_by_info")})', "short",
                  "The site each ingress said served the request (its X-Homelab-Site header). It matches the observer's site, because each site's pods reach their own ingress.", "table", column=None),
                p("Name certificate remaining", metric("homelab:ingress_cert_remaining_seconds"), "s",
                  "Certificate the client saw for each name, per site. Under 14 days alerts; cert-manager renews at 30.", "table"),
            ],
            # Plausible's own numbers, from its exporter (they replace the Plausible
            # reference board). Its site IDs are domains: they appear only at runtime.
            "Analytics": [
                p("Analytics health", f'max by (k8s_cluster, component) ({selector("plausible_health")})', "bool",
                  "Plausible's health API per component (Postgres, ClickHouse, sessions, the sites cache), polled by its exporter. The Plausible probe on User paths is the independent check.",
                  "table", column="Health", words=("Up", "Down")),
                p("Visitors today", f'max by (k8s_cluster, site_id) ({selector("plausible_visitors")})', "short",
                  "Unique visitors per tracked site so far today (Plausible's day period, in the site's time zone). It resets at midnight, so read it as a running count."),
                p("Page views today", f'max by (k8s_cluster, site_id) ({selector("plausible_pageviews")})', "short",
                  "Page views per tracked site so far today."),
                p("Bounce rate today", f'max by (k8s_cluster, site_id) ({selector("plausible_bounce_rate")})', "percent",
                  "Share of today's visits that viewed one page, per tracked site. Zero with no visits."),
                p("Visit duration today", f'max by (k8s_cluster, site_id) ({selector("plausible_visit_duration")})', "s",
                  "Average visit duration today, per tracked site. Zero with no visits."),
            ],
            "Dependencies": [
                p("Certificate validity remaining", f'max by (k8s_cluster, name) ({metric("certmanager_certificate_expiration_timestamp_seconds")}) - time()', "s",
                  "Expiry of certificates managed by cert-manager. S3 transport trust and external identity-provider health are separate checks.", "table"),
                p("Tunnel connections", metric("cloudflared_tunnel_ha_connections"), "short",
                  "Cloudflared connection evidence. This does not by itself certify the origin, identity provider or application content."),
            ],
        }, "Apps", sections=True)
