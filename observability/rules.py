"""Generate normalized evidence rules and executable PromQL behavior fixtures."""

from pathlib import Path

import yaml

from inventory import inventory

ROOT = Path(__file__).resolve().parents[1]
# The SLO window (kubernetes/apps/monitoring/pyrra/app/slos.yaml): inside the
# 15 d local retention, since Pyrra's rules run on each site's Prometheus.
SLO_WINDOW_SECONDS = 14 * 86400


def records():
    db = "k8s_cluster, namespace, db_cluster"
    expected_db = f"max by ({db}) (homelab_expected_database_info)"
    identity = "k8s_cluster, app, kind, workload"
    def fresh(name):
        return f"({name} and (time() - timestamp({name}) < 180))"

    desired = f"sum by (k8s_cluster, app) (max by ({identity}) ({fresh('homelab_workload_desired_replicas')}))"
    ready = f"sum by (k8s_cluster, app) (max by ({identity}) ({fresh('homelab_workload_ready_replicas')}))"
    return {
        "homelab:site_reporting": '(max by (k8s_cluster) (homelab_collection_success{collector="workloads"}) == bool 1) * on (k8s_cluster) (time() - max by (k8s_cluster) (homelab_collection_last_success_timestamp_seconds{collector="workloads"}) < bool 180) * on (k8s_cluster) (time() - max by (k8s_cluster) (homelab_collection_last_success_timestamp_seconds{collector="workloads"}) >= bool 0)',
        "homelab:site_state": '(homelab:site_reporting == 1) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_expected_site_info))',
        "homelab:database_in_recovery": f'max by ({db}) (label_replace(cnpg_pg_replication_in_recovery{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_replay_lag_seconds": f'max by ({db}) (label_replace(cnpg_pg_replication_lag{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_connections": f'sum by ({db}) (label_replace(cnpg_backends_total{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_age_seconds": f'time() - max by ({db}) (label_replace(barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_state": f'((homelab:database_backup_age_seconds >= bool 0) * (homelab:database_backup_age_seconds <= bool 108000) + 3 * (homelab:database_backup_age_seconds > bool 108000)) or on ({db}) (0 * {expected_db})',
        # Where the app runs, nobody needs to take it over: Not applicable (5).
        "homelab:recovery_state": f'(0 * ({desired} > 0) + 5) and on (k8s_cluster, app) (max by (k8s_cluster, app) (homelab_expected_workload_info{{tier="warm"}})) or on (k8s_cluster, app) ' + '(3 * max by (k8s_cluster, app) (' + fresh('homelab_recovery_prerequisite_state') + ' == 3) / 3) or on (k8s_cluster, app) ((count by (k8s_cluster, app) (max by (k8s_cluster, app, prerequisite) (' + fresh('homelab_recovery_prerequisite_state{prerequisite=~"database|backup|restore|fence"}') + ') == 1) == 4) / 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info{tier="warm"}))',
        "homelab:local_history_seconds": 'time() - min by (k8s_cluster) (prometheus_tsdb_lowest_timestamp_seconds)',
        # Share of the SLO window this site's Prometheus holds: below 1, every
        # SLO's availability covers only that much of its window.
        "homelab:slo_window_coverage_ratio": f'clamp_max(homelab:local_history_seconds / {SLO_WINDOW_SECONDS}, 1)',
        # How far behind the archive is: the newest sample it holds for each
        # site. Prometheus cuts 2 h blocks once its head spans 3 h, so 1-3 h is
        # normal; more means uploads, the bucket or the Compactor's sync stalled.
        "homelab:archive_lag_seconds": 'time() - max by (k8s_cluster) (' + fresh('homelab_archive_newest_sample_timestamp_seconds') + ')',
        # History the archive can answer for, per site (Pyrra's 28-35 d windows).
        "homelab:archive_history_seconds": 'time() - min by (k8s_cluster) (' + fresh('homelab_archive_oldest_sample_timestamp_seconds') + ')',
        "homelab:archive_free_ratio": 'min by (k8s_cluster) (garage_local_disk_avail{volume="data"} / garage_local_disk_total{volume="data"})',
        "homelab:unuploaded_block_age_seconds": 'time() - max by (k8s_cluster) (thanos_objstore_bucket_last_successful_upload_time{job=~".*thanos-discovery"} > 0)',
        "homelab:archive_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="archive"} and (time() - timestamp(probe_success{observability_target="archive"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # The broker accepted the collector's login (MQTT), from each site. A
        # global Service: either site's check reaches the broker wherever it runs.
        "homelab:mqtt_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="mqtt"} and (time() - timestamp(probe_success{observability_target="mqtt"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        "homelab:internet_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="internet"} and (time() - timestamp(probe_success{observability_target="internet"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # Replica evidence, not an end-to-end path: 1 healthy, 2 degraded, 3 failed,
        # 4 standby (desired 0, the normal state of an app on the other site).
        "homelab:workload_state": (
            '0 * max by (k8s_cluster, app) (homelab_workload_present == 0) or '
            f'({desired} == 0) + 4'
            f' or ((({ready} >= {desired}) * 0) + 1)'
            f' or ((({ready} < {desired}) > 0) * 0 + 2)'
            f' or (({ready} < {desired}) * 0 + 3)'
            ' or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))'),
        **objective_records(fresh),
        **other_database_records(),
        **network_records(),
        # Each site's collector reaches the shared-VIP names the way a client does
        # (router DNS, verified TLS, the ingress routing the host). A pod reaches
        # its own site's ingress for either VIP, so this is per site, by name.
        "homelab:ingress_state": '(3 - 2 * min by (k8s_cluster, name) (probe_success{observability_target="ingress"} and (time() - timestamp(probe_success{observability_target="ingress"})) < 180)) or on (k8s_cluster, name) (0 * max by (k8s_cluster, name) (homelab_expected_ingress_info))',
        "homelab:ingress_cert_remaining_seconds": 'min by (k8s_cluster, name) (homelab_ingress_cert_expiry_timestamp_seconds) - time()',
        # Content probe from the site that runs the app; Standby (4) where it's
        # deliberately at zero; Unknown where neither is observed.
        "homelab:service_state": '(3 - 2 * min by (k8s_cluster, app) (probe_success{observability_target="service"} and (time() - timestamp(probe_success{observability_target="service"})) < 180)) or on (k8s_cluster, app) (homelab:workload_state == 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))',
        **house_records(),
        **host_records(),
    }


def bands(value, degraded, failed, higher_is_better=True):
    """1 at or past `degraded`, 2 between, 3 past `failed`."""
    if higher_is_better:
        return (f"(({value}) >= bool {degraded}) + 2 * (({value}) < bool {degraded}) * (({value}) >= bool {failed})"
                f" + 3 * (({value}) < bool {failed})")
    return (f"(({value}) <= bool {degraded}) + 2 * (({value}) > bool {degraded}) * (({value}) <= bool {failed})"
            f" + 3 * (({value}) > bool {failed})")


def host_records():
    """Each Windows host's resources as states (Homelab's Hosts tile, L1): the USE
    signals that bite on these hosts, banded. A host whose exporter is down has
    Unknown (0) rows. Site 1's Prometheus scrapes both hosts today; a second
    observer would be reduced like the other states (IDENTITY).

    Each site also grades its own WSL VM (the "WSL ..." resources) under its own
    host's name (WINDOWS_HOST_NAME in cluster-settings), from the node-exporter on
    each of its cluster's nodes: Talos-in-Docker nodes all read the VM's kernel,
    so any node's figures are the VM's. Its node-exporters down: Unknown."""
    by = "k8s_cluster, instance"
    volumes = 'volume!~"HarddiskVolume.+"'
    windows = f'max by ({by}) (up{{job="windows-exporter"}})'
    own = 'k8s_cluster="${CLUSTER_NAME}"'

    def on_host(expr):
        return f'label_replace({expr}, "instance", "' + "${WINDOWS_HOST_NAME}" + '", "", "")'

    vm = on_host(f'max by (k8s_cluster) (up{{job="node-exporter",{own}}})')
    ops = (f'rate(windows_logical_disk_reads_total{{{volumes}}}[15m])'
           f' + rate(windows_logical_disk_writes_total{{{volumes}}}[15m])')
    # Seconds per operation on the slowest volume doing real work (over 1 op/s);
    # an idle disk isn't slow: 0.
    latency = (f'max by ({by}) (rate(windows_logical_disk_read_write_latency_seconds_total{{{volumes}}}[15m])'
               f' / ({ops}) and ({ops}) > 1)'
               f' or on ({by}) (0 * max by ({by}) (windows_logical_disk_reads_total))')
    var = f'mountpoint="/var",{own}'
    # (value, degraded, failed, higher is better, what has to be up for it)
    values = {
        # Sustained, not a spike: 15 minutes.
        "CPU": (f'1 - avg by ({by}) (rate(windows_cpu_time_total{{mode="idle"}}[15m]))', 0.85, 0.95, False, windows),
        # RAM not available to new work (standby cache counts as available), over
        # 15 minutes. Not commit charge: its limit is RAM plus the page file's
        # current size, and an automatically managed page file grows when commit
        # nears it, so commit read 93% of a limit that kept moving while 70% of
        # RAM was in use (the WSL VM commits its whole .wslconfig memory).
        "Memory": ((f'1 - max by ({by}) (avg_over_time(windows_memory_available_bytes[15m]))'
                    f' / max by ({by}) (windows_memory_physical_total_bytes)'), 0.90, 0.97, False, windows),
        "Disk latency": (latency, 0.05, 0.2, False, windows),
        "Disk space": (f'min by ({by}) (windows_logical_disk_free_bytes{{{volumes}}} / windows_logical_disk_size_bytes)',
                       0.10, 0.05, True, windows),
        # The VM's processors (its .wslconfig limit, fewer than the host's).
        "WSL CPU": (on_host(f'max by (k8s_cluster) (1 - avg by (k8s_cluster, instance)'
                            f' (rate(node_cpu_seconds_total{{mode="idle",{own}}}[15m])))'), 0.85, 0.95, False, vm),
        # Page cache counts as available, as on the host.
        "WSL memory": (on_host(f'max by (k8s_cluster) (1 - avg_over_time(node_memory_MemAvailable_bytes{{{own}}}[15m])'
                               f' / node_memory_MemTotal_bytes{{{own}}})'), 0.90, 0.97, False, vm),
        # The VM's ext4 disk (the nodes' /var): its own size limit. The Windows
        # volume holding its VHDX usually fills first, and is Disk space above.
        "WSL disk space": (on_host(f'min by (k8s_cluster) (node_filesystem_avail_bytes{{{var}}}'
                                   f' / node_filesystem_size_bytes{{{var}}})'), 0.10, 0.05, True, vm),
        # Share of time every runnable task stalled on I/O at once (PSI full): slow
        # disk as the pods feel it. Under 1% in the week to 2026-11-08.
        "WSL I/O stall": (on_host(f'max by (k8s_cluster) (rate(node_pressure_io_stalled_seconds_total{{{own}}}[15m]))'),
                          0.10, 0.25, False, vm),
    }
    return {
        # The measured value per resource (ratios, but seconds for disk latency).
        "homelab:host_usage": " or ".join(
            f'label_replace({value}, "resource", "{name}", "", "")' for name, (value, *_) in values.items()),
        "homelab:host_state": " or ".join(
            f'label_replace(({bands(f"homelab:host_usage{{resource=\"{name}\"}}", degraded, failed, higher)})'
            f' or on ({by}) (0 * {expected}), "resource", "{name}", "", "")'
            for name, (_, degraded, failed, higher, expected) in values.items()),
    }


def house_records():
    """The household view (L0): one state per domain, for this site's own share
    only, with no names or identifiers. 1 healthy, 2 degraded, 3 failed; a
    domain this site has no part in has no series. Home Assistant combines the
    sites (its house_systems package): docs/operations/observability-implementation.md,
    "Household view"."""
    own = "and on (k8s_cluster) (max by (k8s_cluster) (homelab_observer_info))"

    def domain(expr, name):
        return f'label_replace({expr}, "domain", "{name}", "", "")'

    def share(now, day, source):
        # All of what worked at some point in the last day is working: 1; some
        # of it: 2; none of it: 3. Something off for a whole day drops out.
        # Only while the source reports at all: a blind exporter (UniFi Poller
        # on a lost site, say) leaves no row, which reads Unknown, not Failed.
        return (f"((({now}) >= bool ({day})) + 2 * (({now}) < bool ({day})) "
                f"or (0 * ({day}) + 3)) and on () ({source})")

    # By camera, not by series: a Frigate pod with a new IP is a new instance
    # label, and the old series would count as a camera gone for a day.
    cameras_now = "count(max by (camera_name) (frigate_camera_fps > 0))"
    cameras_day = "count(max by (camera_name) (max_over_time(frigate_camera_fps[1d]) > 0))"
    aps_now = "count(max by (name) (unpoller_device_uptime_seconds > 0))"
    aps_day = "count(max by (name) (max_over_time(unpoller_device_uptime_seconds[1d]) > 0))"
    active = f"(homelab:service_state != 4 {own})"

    def rank(states):
        # Failed > Degraded > Unknown > Healthy as 4..1, then back to a state.
        return (f"max((({states}) == bool 3) * 4 + (({states}) == bool 2) * 3"
                f" + (({states}) == bool 0) * 2 + (({states}) == bool 1))")

    def rank_state(name):
        return f"({name} == bool 4) * 3 + ({name} == bool 3) * 2 + ({name} == bool 1)"

    # Backups: every app's recovery point and every database backup this site
    # takes. A site that backs nothing up has no part.
    backups = (f'label_replace((homelab:app_rpo_state {own}), "kind", "app", "", "")'
               f' or label_replace((homelab:database_backup_state != 4 != 5 {own}), "kind", "database", "", "")')
    # Smart devices: of the devices Home Assistant had available at some point
    # today, the share available now (95% normal: the worst week seen kept
    # 96.7%). Automations, scripts and helpers aren't devices.
    devices = 'homeassistant_entity_available{domain=~"light|switch|binary_sensor|sensor|climate|lock|cover|fan|media_player|vacuum"}'
    devices_now = f"count(max by (entity) ({devices}) == 1)"
    devices_day = f"count(max by (entity) (max_over_time({devices}[1d])) == 1)"
    # Storage: the fullest Windows drive the site's disks live on (C:, E:).
    free = f'min((windows_logical_disk_free_bytes{{volume=~"C:|E:"}} / windows_logical_disk_size_bytes) {own})'
    return {
        "homelab:house_backups_rank": rank(backups),
        # Worst over the apps this site serves: Failed > Degraded > Unknown >
        # Healthy. A reporting site serving none has nothing failing (1), so
        # every reporting site has an apps row and a missing one stands out.
        "homelab:house_apps_rank": (f"max((({active}) == bool 3) * 4 + (({active}) == bool 2) * 3"
                                    f" + (({active}) == bool 0) * 2 + (({active}) == bool 1))"
                                    " or (0 * max(homelab_observer_info) + 1)"),
        "homelab:house_state": " or ".join([
            domain(f"max(homelab:internet_state {own})", "internet"),
            domain(share(aps_now, aps_day, "count(unpoller_device_uptime_seconds)"), "wifi"),
            domain(f'max(homelab:service_state{{app="home-assistant"}} != 4 {own})', "automation"),
            domain(share(cameras_now, cameras_day, "count(frigate_camera_fps)"), "cameras"),
            domain("(homelab:house_apps_rank == bool 4) * 3 + (homelab:house_apps_rank == bool 3) * 2"
                   " + (homelab:house_apps_rank == bool 1)", "apps"),
            domain(f"3 - 2 * max(homelab:site_reporting {own})", "monitoring"),
            domain(rank_state("homelab:house_backups_rank"), "backups"),
            domain(f"({bands(f'{devices_now} / {devices_day}', 0.95, 0.5)} or (0 * {devices_day} + 3))"
                   f" and on () count({devices})", "devices"),
            domain(bands(free, 0.10, 0.05), "storage"),
            # Away access: the Cloudflare tunnel's connections to the edge (4 normally).
            domain(bands(f"max(cloudflared_tunnel_ha_connections {own})", 4, 1), "away"),
        ]),
    }


def other_database_records():
    """Databases outside CNPG, from their workload's readiness: 1 every desired
    replica ready, 3 not, 4 a deliberate standby (desired 0), 0 unobserved."""
    key = "k8s_cluster, namespace, workload"
    expected = f"max by ({key}, engine, app) (homelab_expected_other_database_info)"

    def per_workload(sts, deploy):
        return (f'max by ({key}) (label_replace({sts}, "workload", "$1", "statefulset", "(.+)")'
                f' or label_replace({deploy}, "workload", "$1", "deployment", "(.+)"))')

    desired = per_workload("kube_statefulset_replicas", "kube_deployment_spec_replicas")
    ready = per_workload("kube_statefulset_status_replicas_ready", "kube_deployment_status_replicas_available")
    return {
        "homelab:other_database_state": (
            f"({expected} * 0 + 4) and on ({key}) ({desired} == 0)"
            f" or ({expected} * 0 + 1) and on ({key}) (({ready} >= {desired}) > 0)"
            f" or ({expected} * 0 + 3) and on ({key}) ({ready} < {desired})"
            f" or ({expected} * 0)"),
    }


def objective_records(fresh):
    """Per-app recovery objectives on each site (inventory.OBJECTIVES).

    Backup age is the app's oldest newest-backup over every scheduled PVC
    (Kopiur's last success for its policy) and every primary database
    (Barman's). A scheduled PVC or a primary without one, or with a timestamp
    in the future, leaves the app Unknown: a fresh backup of one piece can't
    stand in for another. Where nothing is scheduled (a standby), no row.
    """
    db = "k8s_cluster, namespace, db_cluster"
    scheduled = f"max by (k8s_cluster, namespace, policy, app, pvc) ({fresh('homelab_app_backup_scheduled')} == 1)"
    primaries = f"max by ({db}, app) (homelab_expected_database_info) and on ({db}) (homelab:database_in_recovery == 0)"

    def target(objective):
        return f'max by (k8s_cluster, app) (homelab_app_recovery_objective_seconds{{objective="{objective}"}})'

    gap = (f"max by (k8s_cluster, app) ({scheduled} unless on (k8s_cluster, app, pvc) homelab:app_pvc_backup_age_seconds)"
           f" or max by (k8s_cluster, app) ({primaries} unless on ({db}) (homelab:database_backup_age_seconds >= 0))")
    drill = f"max by (k8s_cluster, app) ({fresh('homelab_restore_last_duration_seconds')})"
    running = (f"sum by (k8s_cluster, app) (max by (k8s_cluster, app, kind, workload) "
               f"({fresh('homelab_workload_desired_replicas')}))")
    return {
        "homelab:app_pvc_backup_age_seconds": (
            "((time() - max by (k8s_cluster, namespace, policy) (kopiur_policy_last_backup_success_timestamp_seconds)) >= 0)"
            f" * on (k8s_cluster, namespace, policy) group_right {scheduled}"),
        "homelab:app_backup_age_seconds": (
            "max by (k8s_cluster, app) (homelab:app_pvc_backup_age_seconds or "
            f"((homelab:database_backup_age_seconds >= 0) * on ({db}) group_right max by ({db}, app) ({primaries})))"),
        # 1 within the objective, 3 past it, 0 when a piece has no evidence.
        "homelab:app_rpo_state": (
            f"(0 * ({gap})) or ((homelab:app_backup_age_seconds <= bool on (k8s_cluster, app) {target('rpo')})"
            f" + 3 * (homelab:app_backup_age_seconds > bool on (k8s_cluster, app) {target('rpo')}))"),
        # The last successful standby restore drill against the restore objective;
        # Unknown until a drill reports how long it took. The drill restores into
        # the standby, so where the app runs it's Not applicable (5).
        "homelab:app_rto_state": (
            f"(0 * ({running} > 0) + 5) and on (k8s_cluster, app) {target('rto')}"
            f" or on (k8s_cluster, app) (({drill} <= bool on (k8s_cluster, app) {target('rto')}) + 3 * ({drill} > bool on (k8s_cluster, app) {target('rto')}))"
            f" or on (k8s_cluster, app) (0 * {target('rto')})"),
    }


def network_records():
    """Per-site network path, by hop: 1 healthy, 2 degraded, 3 failed, 0 Unknown.

    tunnel: the router's WireGuard peer for the site's distro handshook within
    180 s (WireGuard rekeys every 2 min while traffic flows). router-bgp: the
    router's session with the distro's FRR is established; with the router
    observed but the session missing, it's down (RouterOS lists only live
    sessions). node-bgp: the Cilium agents' sessions with the distro, all
    (healthy), some (degraded) or none (failed). The router is observed by
    homelab-wsl's mktxp only, so homelab-wsl-02's rules leave those Unknown and
    the dashboards keep the real answer. mesh: the site's Cilium agents and its
    KVStoreMesh both have the other cluster ready.
    """
    sites = inventory()["sites"]

    def per_site(expr, site):
        return f'label_replace({expr}, "k8s_cluster", "{site["cluster"]}", "", "")'

    def hop(name, expr):
        return f'label_replace({expr}, "hop", "{name}", "", "")'

    router_up = "max(mktxp_system_uptime)"
    tunnel = " or ".join(per_site(
        f'3 - 2 * (min(mktxp_last_handshake{{interface="{s["tunnel"]}"}}) < bool 180)', s) for s in sites)
    router_bgp = " or ".join(per_site(
        f'(3 - 2 * max(mktxp_bgp_established{{name=~"{s["router_peer"]}(-[0-9]+)?"}}))'
        f' or (0 * {router_up} + 3)', s) for s in sites)
    nodes = 'cilium_bgp_control_plane_session_state{source_site=""}'
    node_bgp = (f'3 - (min by (k8s_cluster) ({nodes}) == bool 1)'
                f' - (max by (k8s_cluster) ({nodes}) == bool 1)')
    # Tagged apart first: `or` would drop KVStoreMesh's series, whose labels
    # match the agents'.
    ready = ('(min by (k8s_cluster) ('
             'label_replace(cilium_clustermesh_remote_cluster_readiness_status{source_site=""}, "reader", "agent", "", "")'
             ' or label_replace(cilium_kvstoremesh_remote_cluster_readiness_status{source_site=""}, "reader", "kvstoremesh", "", "")'
             ') == bool 1)')
    # Readiness is the control plane only. In the partition drill (2026-11-07)
    # site 2 reached site 1's clustermesh-apiserver around its downed tunnel
    # (eth0, the LAN, the router) and stayed ready while pod traffic between
    # the sites failed. So the site's own probe of its peer (pod to pod,
    # across the mesh) must pass too; without one, readiness alone decides.
    probe = ('min by (k8s_cluster) (label_replace(homelab_peer_probe_success{source_site=""},'
             ' "k8s_cluster", "$1", "observer_cluster", "(.+)"))')
    mesh = f"3 - 2 * (({ready} * on (k8s_cluster) {probe}) or {ready})"
    expected = "max by (k8s_cluster) (homelab_expected_site_info)"
    hops = (("tunnel", tunnel), ("router-bgp", router_bgp), ("node-bgp", node_bgp), ("mesh", mesh))
    known = " or ".join(hop(n, e) for n, e in hops)
    unknown = " or ".join(hop(n, f"0 * {expected}") for n, _ in hops)
    # Which site the router sends each shared VIP to: its preferred site while
    # that site carries routes, else the other one while it does. A site
    # carries routes while its session is up and it sends the router at least
    # one prefix: the distro's FRR keeps the session up with the site's nodes
    # down, and announces nothing (site-loss drill, 2026-11-07). Inferred from
    # those and the declared local preference (100/50), not read from the
    # router's route table. 1 routed, 0 not, absent Unknown.
    session = 'min(homelab:network_path_state{{hop="router-bgp",k8s_cluster="{}"}} > 0)'
    sent = 'sum(mktxp_bgp_prefix_count{{name=~"{}(-[0-9]+)?"}})'
    peer = {s["cluster"]: s["router_peer"] for s in sites}

    def carries(site):
        return f"(({session.format(site)} == bool 1) * ({sent.format(peer[site])} > bool 0))"

    routes = []
    for vip in inventory()["vips"]:
        first = vip["preferred"]
        other = next(s["cluster"] for s in sites if s["cluster"] != first)
        for site, expr in ((first, carries(first)),
                           (other, f"{carries(other)} * ({carries(first)} != bool 1)")):
            routes.append(f'label_replace(label_replace({expr}, "k8s_cluster", "{site}", "", ""), "vip", "{vip["address"]}", "", "")')
    prefixes = " or ".join(per_site(
        f'sum(mktxp_bgp_prefix_count{{name=~"{s["router_peer"]}(-[0-9]+)?"}})', s) for s in sites)
    return {
        "homelab:network_path_state": f"({known}) or on (k8s_cluster, hop) ({unknown})",
        "homelab:router_bgp_prefixes": prefixes,
        "homelab:shared_vip_route": " or ".join(routes),
    }


# The capacity plan of homelab-wsl-02: the room of one node container, what
# Loki takes in for its 90-day streams, and the drive under the WSL disk.
#
# The node container of worker 1 and its Docker memory limit, in GiB
# (talos_clusters.wsl-02.memory_nodes in the Ansible inventory). No series
# carries that limit, so it is stated again here.
CAPACITY_NODE = "homelab-wsl-02-worker-1"
CAPACITY_NODE_LIMIT_GIB = 18
# Loki there keeps a stream with the label security_stream for 90 days
# (kubernetes/clusters/wsl-02/apps), and counts the bytes it takes in by
# retention period, before compression. The plan for those streams, in the
# same unit: 1.2 GB of lines a day. That is 0.8 GB of audit logs (0.4 GB for
# each of the two clusters), 0.09 GB of router log, 0.09 GB of scan findings
# and 0.2 GB of the cloud trail (100,000 events of 2 kB).
SECURITY_RETENTION_HOURS = 90 * 24
SECURITY_INGEST_PLAN_GB = 1.2
# The Windows host of that cluster, and the drive that holds its WSL disk.
CAPACITY_DRIVE_HOST = "WSL-02"
CAPACITY_DRIVE_VOLUME = "C:"
CAPACITY_DRIVE_FLOOR_GB = 100


def capacity_alerts():
    """Each alert reads a series that only one site's Prometheus has, so the rule
    is quiet on the other site: the kubelet's node figure and Loki's own series
    are scraped on homelab-wsl-02, and the Windows hosts by homelab-wsl."""
    def board(uid):
        return f'https://grafana.${{INTERNAL_DOMAIN}}/d/{uid}?var-cluster={{{{ $labels.k8s_cluster }}}}'

    return [
        # The working set of the node's root cgroup, which is the node container
        # (a private cgroup namespace): what the kubelet reports for its node.
        {"alert": "NodeContainerMemoryNearLimit",
         "expr": (f'max by (k8s_cluster, node) (node_memory_working_set_bytes{{node="{CAPACITY_NODE}"}})'
                  f' > 0.9 * {CAPACITY_NODE_LIMIT_GIB} * 1024^3'),
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": "{{ $labels.node }} is above 90% of the memory limit of its node container",
                         "dashboard_url": board("tnwks-kubernetes"),
                         "description": (f"The node container has a memory limit of {CAPACITY_NODE_LIMIT_GIB} GiB. The scheduler does not see it,"
                                         " because the node reports the memory of the whole WSL VM. Find the largest pods of the node,"
                                         " and move or shrink one. The limit is memory_nodes of talos_clusters in the Ansible inventory.")}},
        {"alert": "LokiSecurityIngestAbovePlan",
         "expr": ('sum by (k8s_cluster) (increase(loki_distributor_bytes_received_total'
                  f'{{retention_hours="{SECURITY_RETENTION_HOURS}"}}[1d])) > 2 * {SECURITY_INGEST_PLAN_GB}e9'),
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "Loki on {{ $labels.k8s_cluster }} took in more than twice the planned bytes of its 90-day streams in a day",
                         "dashboard_url": board("tnwks-observability"),
                         "description": (f"The streams that Loki keeps for 90 days took in more than {2 * SECURITY_INGEST_PLAN_GB:g} GB of lines"
                                         f" in 24 hours, before compression. The plan is {SECURITY_INGEST_PLAN_GB:g} GB a day. Find the stream that grew"
                                         " (the label security_stream), then lower its volume at the source or its retention.")}},
        {"alert": "WslDriveSpaceLow",
         "expr": ('min by (k8s_cluster, instance, volume) (windows_logical_disk_free_bytes'
                  f'{{instance="{CAPACITY_DRIVE_HOST}",volume="{CAPACITY_DRIVE_VOLUME}"}}) < {CAPACITY_DRIVE_FLOOR_GB}e9'),
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": f"The drive under the WSL disk of {{{{ $labels.instance }}}} has less than {CAPACITY_DRIVE_FLOOR_GB} GB free",
                         "dashboard_url": board("tnwks-hosts"),
                         "description": ("{{ $labels.volume }} of {{ $labels.instance }} holds the WSL disk file of its cluster, and WSL stops when that drive is full."
                                         " The stores of the cluster delete at a set age: lower the age of the largest one, or free space on the drive.")}},
    ]


# The certificates of IAM Roles Anywhere behind the AWS sessions of the security
# platform (kubernetes/apps/security/aws-credentials). The refresher there writes
# the end of each certificate to the renew time of a Lease, and kube-state-metrics
# serves that time: a CronJob has no endpoint of its own to scrape.
SECURITY_CERTIFICATE_LEASES = "aws-security-.+-certificate"
SECURITY_CERTIFICATE_DAYS = 30


# The sink of the Vector aggregator that writes the audit log of the API
# server to the Loki of the security streams (component_id in
# kubernetes/apps/monitoring/vector/aggregator/config/vector.yaml).
AUDIT_SINK = "loki_security_audit"
AUDIT_SINK_WINDOW = "15m"


# The series of the security converter about the list of accepted findings
# (security/converter/README.md, "A file that gives no entry" and "An entry that
# the converter skips"). The converter runs on homelab-wsl-02 only.
ACCEPTED_LIST_READ = "homelab_security_converter_accepted_list_read"
ACCEPTED_SKIPPED = "homelab_security_converter_accepted_skipped_entries"


def security_alerts():
    """The certificate alert reads a series of homelab-wsl-02 only, where the
    refresher runs. The audit alert reads a series of the Vector aggregator of
    each site.

    Vector counts an error of a sink only when it stops trying: a request that
    Loki refused (HTTP 400) is not sent again, and a request that got no answer
    is sent again and is not an error here. The series of the counter starts at
    its first error, with the value 1, so increase() over the new series is 0.
    The second term of the audit alert fires for a series that has a value now
    and had none one window before. Both terms keep the labels of the pod, so
    the counter of a new pod is a new series; the sum then takes them away."""
    errors = f'vector_component_errors_total{{component_id="{AUDIT_SINK}"}}'
    return [
        {"alert": "SecurityCertificateExpiring",
         "expr": ('min by (k8s_cluster, namespace, lease) (kube_lease_renew_time'
                  f'{{namespace="security",lease=~"{SECURITY_CERTIFICATE_LEASES}"}}) - time()'
                  f' < {SECURITY_CERTIFICATE_DAYS} * 86400'),
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": (f"A certificate for IAM Roles Anywhere on {{{{ $labels.k8s_cluster }}}} ends within"
                                     f" {SECURITY_CERTIFICATE_DAYS} days: {{{{ $labels.lease }}}}"),
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "description": ("The AWS sessions of one tool of the security platform come from this certificate, and stop when it ends."
                                         " Make a new certificate and put it in its SOPS Secret:"
                                         " kubernetes/apps/security/aws-credentials/README.md has the steps."
                                         " The alert ends at the next run of the refresher.")}},
        {"alert": "SecurityAuditLinesRefused",
         "expr": ("sum by (k8s_cluster, component_id) ("
                  f"increase({errors}[{AUDIT_SINK_WINDOW}]) > 0"
                  f" or ({errors} > 0 unless {errors} offset {AUDIT_SINK_WINDOW}))"),
         "labels": {"severity": "warning"},
         "annotations": {"summary": "Loki refused audit lines of {{ $labels.k8s_cluster }}",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "description": ("The sink {{ $labels.component_id }} of the Vector aggregator on {{ $labels.k8s_cluster }} dropped a request"
                                         f" in the last {AUDIT_SINK_WINDOW[:-1]} minutes, and Vector does not send it again. Loki refuses a line that is older than"
                                         " 7 days, or more than one hour older than the newest line of its stream. The log of the aggregator pod has the"
                                         " answer of Loki, and vector_component_discarded_events_total has the number of lines.")}},
        # A list that gives no entry mutes no Trivy finding and shows no accepted
        # finding on the board, while a Prowler run can still mute: no check may
        # go silent. With no list at all the converter serves 1 and 0, so no alert.
        {"alert": "SecurityAcceptedListUnread",
         "expr": f'max by (k8s_cluster) ({ACCEPTED_LIST_READ}{{source_site=""}}) == 0',
         "for": "30m", "labels": {"severity": "warning"},
         "annotations": {"summary": "The list of accepted findings on {{ $labels.k8s_cluster }} gives no entry",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "description": ("The security converter can not read the list of accepted findings in the Secret"
                                         " security-accepted-findings, so it serves no accepted finding: the board shows none, and"
                                         " the rule of Trivy Operator mutes none. The log of the converter has the number of the line"
                                         " and the kind of its form. security/converter/README.md, \"The form that the converter"
                                         " reads\", has the form. The alert ends in the cycle after the list is fixed.")}},
        {"alert": "SecurityAcceptedEntriesSkipped",
         "expr": f'max by (k8s_cluster) ({ACCEPTED_SKIPPED}{{source_site=""}}) > 0',
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "Entries of the list of accepted findings on {{ $labels.k8s_cluster }} are skipped",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "description": ("{{ $value }} entries of the list of accepted findings do not fit, so the security converter"
                                         " and the Prowler runs skip them: their findings show as fails. The log of the converter"
                                         " has the number of each entry and why. security/converter/README.md, \"An entry that the"
                                         " converter skips\", has the rules. The alert ends in the cycle after the list is fixed.")}},
    ]


def rule_resource():
    rules = [{"record": name, "expr": expr} for name, expr in records().items()]
    rules += [
        {"alert": "ObservabilityEvidenceStale",
         "expr": '(homelab_collection_success == 0) or (time() - homelab_collection_last_success_timestamp_seconds > 180)',
         "for": "5m", "labels": {"severity": "warning"},
         "annotations": {"summary": "A monitoring collector is no longer observing its upstream",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Scrape success does not establish fresh upstream evidence. Check the collector source and network access."}},
        {"alert": "ObservabilityExpectedDatabaseBackupMissing",
         # Each site only answers for its own primaries: the peer's databases
         # are Unknown here by design, and a replica cluster takes no backups.
         "expr": 'homelab:database_backup_state{k8s_cluster="${CLUSTER_NAME}"} == 0 unless on (k8s_cluster, namespace, db_cluster) (homelab:database_in_recovery == 1)',
         "for": "30m",
         "labels": {"severity": "warning"},
         "annotations": {"summary": "Expected database has no backup evidence",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-data?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "No Barman plugin timestamp is available for this expected database. Another database's fresh backup cannot satisfy its recovery requirement."}},
        {"alert": "ObservabilityArchiveUnavailable", "expr": 'probe_success{observability_target="archive"} == 0',
         "for": "10m", "labels": {"severity": "warning"},
         "annotations": {"summary": "The WSL-02 metrics archive is unavailable",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Local metrics and alerts remain available. Restore archive uploads before retained local blocks expire."}},
        {"alert": "ObservabilityIngressNameFailing", "expr": 'homelab:ingress_state == 3',
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": "A shared-VIP name fails from {{ $labels.k8s_cluster }}: {{ $labels.name }}",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "That site's collector couldn't use the name as a client would: the router's DNS answer, the certificate, or the site's ingress routing the host. homelab_ingress_dns_match and homelab_ingress_http_status say which."}},
        {"alert": "ObservabilityIngressCertificateExpiring", "expr": 'homelab:ingress_cert_remaining_seconds < 14 * 86400',
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "The certificate for {{ $labels.name }} on {{ $labels.k8s_cluster }} expires within 14 days",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "cert-manager renews at 30 days left. Check that site's Certificate and its ACME challenge."}},
        {"alert": "ObservabilityArchiveBehind", "expr": 'homelab:archive_lag_seconds > 14400',
         "for": "30m", "labels": {"severity": "warning"},
         "annotations": {"summary": "The metrics archive's newest data for a site is over 4 hours old",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Normal is 1-3 hours. Check that site's Thanos sidecar uploads (thanos_shipper_upload_failures_total) and the Compactor's bucket sync."}},
        {"alert": "ObservabilityArchiveSpaceLow", "expr": 'homelab:archive_free_ratio < 0.1',
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "The metrics archive has under 10% free space",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Garage shares its disk with the WSL-02 node. Shorten the Compactor's retention or free space before uploads fail."}},
    ]
    return {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "homelab-observability", "namespace": "monitoring"},
            "spec": {"groups": [{"name": "homelab.evidence", "interval": "1m", "rules": rules},
                                {"name": "homelab.capacity", "interval": "1m", "rules": capacity_alerts()},
                                {"name": "homelab.security", "interval": "1m", "rules": security_alerts()}]}}


def fixtures():
    from dashboards import coverage
    header = {panel.title: panel.expr for panel in coverage()}
    # Real cases: two same-name DBs in distinct sites, missing expected DB,
    # stale upstream despite successful scrape, and absent fence/restore evidence.
    return {"rule_files": ["rules-for-test.yaml"], "evaluation_interval": "1m", "tests": [
        {"name": "header counts distinct selected sites, not observers", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_site_info{{k8s_cluster="{site}",observer="{observer}"}}', "values": "1+0x10"}
               for site in ("one", "two") for observer in ("a", "b")],
             *[{"series": f'homelab:site_reporting{{k8s_cluster="{site}",observer="{observer}"}}', "values": "1+0x10"}
               for site in ("one", "two") for observer in ("a", "b")],
         ], "promql_expr_test": [
             {"expr": expr.replace("$cluster", scope), "eval_time": "5m",
              "exp_samples": [{"labels": "{}", "value": value}]}
             for expr in header.values() if expr in (header["Expected sites"], header["Reporting sites"])
             for scope, value in (("one", 1), (".*", 2))
         ]},
        {"name": "future observer timestamps are not current evidence", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "10000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:site_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:site_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "stale complete prerequisites cannot certify recovery", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="{name}"}}', "values": "1 _ _ _ _ _"}
               for name in ("database", "backup", "restore", "fence")],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "4m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "one missing component does not leave a multi-workload app healthy", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test"}', "values": "1+0x10"},
             {"series": 'homelab_workload_present{k8s_cluster="one",app="test",workload="missing"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="test",workload="running"}', "values": "1+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="test",workload="running"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "duplicate collectors do not invent desired replicas", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test"}', "values": "1+0x10"},
             *[{"series": f'homelab_workload_desired_replicas{{k8s_cluster="one",app="test",workload="running",pod="{pod}"}}', "values": "1+0x10"}
               for pod in ("old", "new")],
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="test",workload="running",pod="new"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="test"}', "value": 1}]},
         ]},
        {"name": "same database name and missing backup remain distinct", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "30+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 1},
                 {"labels": 'homelab:database_backup_state{k8s_cluster="two",namespace="databases",db_cluster="app-pg"}', "value": 0},
             ]},
         ]},
        {"name": "successful scrape cannot refresh a stale upstream", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:site_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:site_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "recovery: Not applicable where the warm app runs; a parked site still needs evidence", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="{site}",app="test",tier="warm"}}', "values": "1+0x10"}
               for site in ("one", "two")],
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="test",kind="deployment",workload="test"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="two",app="test",kind="deployment",workload="test"}', "values": "0+0x10"},
             {"series": 'homelab_recovery_prerequisite_state{k8s_cluster="two",app="test",prerequisite="backup"}', "values": "3+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 5},
                 {"labels": 'homelab:recovery_state{k8s_cluster="two",app="test"}', "value": 3}]},
         ]},
        {"name": "absent fencing or restore evidence cannot certify readiness", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             {"series": 'homelab_recovery_prerequisite_state{k8s_cluster="one",app="test",prerequisite="database"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "stale probes cannot certify current paths", "interval": "1m",
         "input_series": [
             {"series": 'homelab_observer_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",observability_target="archive"}', "values": "1 _ _ _ _ _"},
         ], "promql_expr_test": [
             {"expr": "homelab:archive_state", "eval_time": "4m", "exp_samples": [
                 {"labels": 'homelab:archive_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "archive lag is per source site, and Unknown once the index goes stale", "interval": "1m",
         "input_series": [
             {"series": 'homelab_archive_newest_sample_timestamp_seconds{k8s_cluster="one",observer_cluster="a"}', "values": "0+0x2"},
             {"series": 'homelab_archive_newest_sample_timestamp_seconds{k8s_cluster="one",observer_cluster="b"}', "values": "-60+0x2"},
         ], "promql_expr_test": [
             # Two observers report the same archive; lag is per source site and
             # goes absent (Unknown) once the index stops being read.
             {"expr": "homelab:archive_lag_seconds", "eval_time": "2m", "exp_samples": [
                 {"labels": 'homelab:archive_lag_seconds{k8s_cluster="one"}', "value": 120}]},
             {"expr": "homelab:archive_lag_seconds", "eval_time": "6m", "exp_samples": []},
         ]},
        {"name": "network path by hop: stale tunnel, missing router session, partial node sessions", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_last_handshake{interface="wg-site1"}', "values": "30+0x10"},
             {"series": 'mktxp_last_handshake{interface="wg-site2"}', "values": "400+0x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site1-1"}', "values": "1+0x10"},
             {"series": 'cilium_bgp_control_plane_session_state{k8s_cluster="homelab-wsl",node="a"}', "values": "1+0x10"},
             {"series": 'cilium_bgp_control_plane_session_state{k8s_cluster="homelab-wsl",node="b"}', "values": "0+0x10"},
             {"series": 'cilium_clustermesh_remote_cluster_readiness_status{k8s_cluster="homelab-wsl",target_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             {"series": 'cilium_kvstoremesh_remote_cluster_readiness_status{k8s_cluster="homelab-wsl",target_cluster="homelab-wsl-02"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:network_path_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="tunnel"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="tunnel"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="router-bgp"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="router-bgp"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="node-bgp"}', "value": 2},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="node-bgp"}', "value": 0},
                 # The agents see the peer, KVStoreMesh doesn't: failed, not averaged.
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="mesh"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="mesh"}', "value": 0},
             ]},
         ]},
        {"name": "mesh hop: ready but the peer probe fails is Failed; no probe falls back to readiness", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             # Both sites' control planes ready.
             *[{"series": f'cilium_{kind}_remote_cluster_readiness_status{{k8s_cluster="{site}",target_cluster="x"}}', "values": "1+0x10"}
               for site in ("homelab-wsl", "homelab-wsl-02") for kind in ("clustermesh", "kvstoremesh")],
             # Site 2's probe of site 1 fails (partitioned, pod traffic down);
             # site 1 has no probe series at all.
             {"series": 'homelab_peer_probe_success{k8s_cluster="homelab-wsl",observer_cluster="homelab-wsl-02"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:network_path_state{hop="mesh"}', "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="mesh"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="mesh"}', "value": 3},
             ]},
         ]},
        {"name": "shared VIPs follow the preferred site's session, else the other's", "interval": "1m",
         "input_series": [
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site2-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site1-1"}', "values": "7+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site2-1"}', "values": "7+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:shared_vip_route", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.10"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.10"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.200"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.200"}', "value": 1},
             ]},
         ]},
        {"name": "shared VIPs: a site whose session is up but sends no prefixes carries nothing", "interval": "1m",
         "input_series": [
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site1-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site2-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site1-1"}', "values": "7+0x10"},
             # Site 2's nodes are down; its distro FRR still holds the session.
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site2-1"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:shared_vip_route", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.10"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.10"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.200"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.200"}', "value": 0},
             ]},
         ]},
        {"name": "ingress names: passing, failing, unobserved", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_ingress_info{{k8s_cluster="{site}",name="{name}"}}', "values": "1+0x10"}
               for site in ("one", "two") for name in ("a", "b")],
             {"series": 'probe_success{k8s_cluster="one",name="a",observability_target="ingress"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",name="b",observability_target="ingress"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:ingress_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:ingress_state{k8s_cluster="one",name="a"}', "value": 1},
                 {"labels": 'homelab:ingress_state{k8s_cluster="one",name="b"}', "value": 3},
                 {"labels": 'homelab:ingress_state{k8s_cluster="two",name="a"}', "value": 0},
                 {"labels": 'homelab:ingress_state{k8s_cluster="two",name="b"}', "value": 0},
             ]},
         ]},
        {"name": "an unobserved router leaves the router hops Unknown", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:network_path_state{hop=~"tunnel|router-bgp"}', "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="tunnel"}', "value": 0},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="router-bgp"}', "value": 0},
             ]},
         ]},
        {"name": "service state: probed, failing, standby, unobserved", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="one",app="{a}"}}', "values": "1+0x10"}
               for a in ("up", "broken", "parked", "unseen")],
             {"series": 'probe_success{k8s_cluster="one",app="up",observability_target="service"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",app="broken",observability_target="service"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:service_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="up"}', "value": 1},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="broken"}', "value": 3},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="parked"}', "value": 4},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="unseen"}', "value": 0},
             ]},
         ]},
        {"name": "duplicate observations of one prerequisite are not four prerequisites", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="database",observer="{i}"}}', "values": "1+0x10"} for i in range(4)],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "workload state tells standby from failed and keeps missing apps Unknown", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="one",app="{a}"}}', "values": "1+0x10"}
               for a in ("up", "partial", "down", "parked", "missing")],
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="up"}', "values": "2+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="up"}', "values": "2+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="partial"}', "values": "2+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="partial"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="down"}', "values": "1+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="down"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="up"}', "value": 1},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="partial"}', "value": 2},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="down"}', "value": 3},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="parked"}', "value": 4},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="missing"}', "value": 0},
             ]},
         ]},
        {"name": "only a local primary without backup evidence alerts", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="missing-pg"}', "values": "1+0x40"},
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="replica-pg"}', "values": "1+0x40"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="peer-pg"}', "values": "1+0x40"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="replica-pg-1"}', "values": "1+0x40"},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityExpectedDatabaseBackupMissing", "eval_time": "35m", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": "one", "namespace": "databases", "db_cluster": "missing-pg"},
                  "exp_annotations": {"summary": "Expected database has no backup evidence",
                                      "dashboard_url": "https://grafana.${INTERNAL_DOMAIN}/d/tnwks-data?var-cluster=one",
                                      "description": "No Barman plugin timestamp is available for this expected database. Another database's fresh backup cannot satisfy its recovery requirement."}},
             ]},
         ]},
        {"name": "future backup evidence is unknown and peer federation is not counted twice", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "1000+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "0+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1",source_site="one"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 0}]},
             {"expr": "homelab:database_in_recovery", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_in_recovery{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 0}]},
         ]},
        {"name": "an idle database is not a failed one", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "values": "1+0x40"},
             {"series": 'cnpg_backends_total{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "0+0x40"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "0+0x40"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "100+0x40"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_connections", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_connections{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "value": 0}]},
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "value": 1}]},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityExpectedDatabaseBackupMissing", "eval_time": "35m", "exp_alerts": []},
         ]},
        {"name": "no traffic is not an outage: the probe decides", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="quiet"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",app="quiet",observability_target="service"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:service_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="quiet"}', "value": 1}]},
         ]},
        {"name": "an expiring credential alerts before cert-manager's renewal is missed", "interval": "5m",
         "input_series": [
             {"series": 'homelab_ingress_cert_expiry_timestamp_seconds{k8s_cluster="one",name="soon"}', "values": "864000+0x20"},
             {"series": 'homelab_ingress_cert_expiry_timestamp_seconds{k8s_cluster="one",name="later"}', "values": "3456000+0x20"},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityIngressCertificateExpiring", "eval_time": "70m", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": "one", "name": "soon"},
                  "exp_annotations": {"summary": "The certificate for soon on one expires within 14 days",
                                      "dashboard_url": "https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster=one",
                                      "description": "cert-manager renews at 30 days left. Check that site's Certificate and its ACME challenge."}},
             ]},
         ]},
        {"name": "a failed restore drill fails recovery even with everything else ready", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="{name}"}}', "values": f"{value}+0x10"}
               for name, value in (("database", 1), ("backup", 1), ("fence", 1), ("restore", 3))],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 3}]},
         ]},
        *objective_fixtures(),
        *other_database_fixtures(),
        *house_fixtures(),
        *host_fixtures(),
        *capacity_fixtures(),
        *security_fixtures(),
        *audit_sink_fixtures(),
        *accepted_list_fixtures(),
    ]}


def security_fixtures():
    site = "homelab-wsl-02"
    day = 86400
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityCertificateExpiring")

    def lease(name, ends, namespace="security"):
        """A Lease whose renew time is `ends` seconds after the start of the test."""
        return {"series": f'kube_lease_renew_time{{k8s_cluster="{site}",namespace="{namespace}",lease="{name}"}}',
                "values": f"{ends}+0x320"}

    def expected(name):
        labels = {"k8s_cluster": site, "namespace": "security", "lease": name}
        annotations = {}
        for key, text in rule["annotations"].items():
            for label, value in labels.items():
                text = text.replace("{{ $labels." + label + " }}", value)
            annotations[key] = text
        return {"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}

    return [
        {"name": "a certificate with 29 days left alerts; one with 40 days, another lease and another namespace do not",
         "interval": "5m",
         "input_series": [
             lease("aws-security-audit-certificate", 29 * day),
             lease("aws-security-trail-certificate", 40 * day),
             lease("some-controller", 0),
             lease("aws-security-audit-certificate", day, namespace="other"),
         ], "alert_rule_test": [
             {"alertname": "SecurityCertificateExpiring", "eval_time": "30m", "exp_alerts": []},
             {"alertname": "SecurityCertificateExpiring", "eval_time": "70m", "exp_alerts": [
                 expected("aws-security-audit-certificate")]},
         ]},
        # 31 days left at the start: the last 30 days begin after one day.
        {"name": "the alert starts when a certificate has 30 days left, and not before", "interval": "5m",
         "input_series": [lease("aws-security-trail-certificate", 31 * day)],
         "alert_rule_test": [
             {"alertname": "SecurityCertificateExpiring", "eval_time": "23h", "exp_alerts": []},
             {"alertname": "SecurityCertificateExpiring", "eval_time": "26h", "exp_alerts": [
                 expected("aws-security-trail-certificate")]},
         ]},
    ]


def audit_sink_fixtures():
    site = "homelab-wsl"
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityAuditLinesRefused")

    def errors(values, component=AUDIT_SINK, pod="vector-aggregator-1"):
        return {"series": (f'vector_component_errors_total{{k8s_cluster="{site}",namespace="monitoring",pod="{pod}",'
                           f'component_id="{component}",component_kind="sink",component_type="loki",'
                           'error_type="request_failed",stage="sending"}'),
                "values": values}

    labels = {"k8s_cluster": site, "component_id": AUDIT_SINK}
    annotations = {}
    for key, text in rule["annotations"].items():
        for label, value in labels.items():
            text = text.replace("{{ $labels." + label + " }}", value)
        annotations[key] = text
    fires = [{"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}]

    def at(minute, exp_alerts):
        return {"alertname": rule["alert"], "eval_time": f"{minute}m", "exp_alerts": exp_alerts}

    return [
        # Vector makes the series at the first refused request: no sample for
        # 20 minutes, then the value 1. The alert holds for one window.
        {"name": "the first refused request of the audit sink alerts, though its counter starts at 1", "interval": "1m",
         "input_series": [errors("_x20 1+0x40")],
         "alert_rule_test": [at(19, []), at(20, fires), at(34, fires), at(36, [])]},
        {"name": "a later refused request of the audit sink alerts", "interval": "1m",
         "input_series": [errors("1+0x40 2+0x40")],
         "alert_rule_test": [at(30, []), at(41, fires), at(54, fires), at(56, [])]},
        # The pod of the aggregator starts again, and the counter of the new pod
        # is a new series that starts at 1, under the old value.
        {"name": "the first refused request of a new aggregator pod alerts", "interval": "1m",
         "input_series": [errors("3+0x30 stale"), errors("_x40 1+0x20", pod="vector-aggregator-2")],
         "alert_rule_test": [at(30, []), at(39, []), at(40, fires)]},
        {"name": "a counter of the audit sink that does not move does not alert", "interval": "1m",
         "input_series": [errors("2+0x60")],
         "alert_rule_test": [at(30, []), at(60, [])]},
        {"name": "a refused request of another sink does not alert", "interval": "1m",
         "input_series": [errors("_x20 1+1x40", component="loki_kubernetes")],
         "alert_rule_test": [at(20, []), at(30, [])]},
    ]


def accepted_list_fixtures():
    site = "homelab-wsl-02"
    target = f'k8s_cluster="{site}",job="security-converter",namespace="security",service="security-converter"'

    def fires(name, value):
        rule = next(rule for rule in security_alerts() if rule["alert"] == name)
        annotations = {key: text.replace("{{ $labels.k8s_cluster }}", site).replace("{{ $value }}", value)
                       for key, text in rule["annotations"].items()}
        return [{"exp_labels": {"severity": "warning", "k8s_cluster": site}, "exp_annotations": annotations}]

    def lists(read, skipped):
        return [{"series": f"{ACCEPTED_LIST_READ}{{{target}}}", "values": read},
                {"series": f"{ACCEPTED_SKIPPED}{{{target}}}", "values": skipped}]

    def at(name, minute, exp_alerts):
        return {"alertname": name, "eval_time": f"{minute}m", "exp_alerts": exp_alerts}

    unread, skipped = "SecurityAcceptedListUnread", "SecurityAcceptedEntriesSkipped"
    return [
        {"name": "a list that gives no entry alerts after 30 minutes", "interval": "1m",
         "input_series": lists("0+0x90", "0+0x90"),
         "alert_rule_test": [at(unread, 29, []), at(unread, 31, fires(unread, "0")), at(skipped, 90, [])]},
        {"name": "skipped entries alert after one hour", "interval": "1m",
         "input_series": lists("1+0x90", "2+0x90"),
         "alert_rule_test": [at(skipped, 59, []), at(skipped, 61, fires(skipped, "2")), at(unread, 90, [])]},
        # The converter serves the same two values for a list that it read with no
        # entry skipped, and for no list at all (no Secret).
        {"name": "a list that is read with nothing skipped, or no list, does not alert", "interval": "1m",
         "input_series": lists("1+0x150", "0+0x150"),
         "alert_rule_test": [at(unread, 150, []), at(skipped, 150, [])]},
        {"name": "a list that is fixed within 30 minutes does not alert", "interval": "1m",
         "input_series": lists("0+0x20 1+0x60", "3+0x40 0+0x40"),
         "alert_rule_test": [at(unread, 31, []), at(skipped, 61, [])]},
    ]


def capacity_fixtures():
    site = "homelab-wsl-02"
    gib = 1024 ** 3

    def expected(name, **labels):
        """The alert as promtool sees it: its annotations with the labels filled in."""
        rule = next(rule for rule in capacity_alerts() if rule["alert"] == name)
        labels = {"k8s_cluster": site, **labels}
        annotations = {}
        for key, text in rule["annotations"].items():
            for label, value in labels.items():
                text = text.replace("{{ $labels." + label + " }}", value)
            annotations[key] = text
        return {"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}

    def node(name, value, extra=""):
        return {"series": f'node_memory_working_set_bytes{{k8s_cluster="{site}",node="{name}"{extra}}}',
                "values": f"{value}+0x30"}

    def loki(retention_hours, per_minute):
        return {"series": (f'loki_distributor_bytes_received_total{{k8s_cluster="{site}",tenant="fake",'
                           f'retention_hours="{retention_hours}"}}'),
                "values": f"0+{per_minute}x1560"}

    def drive(instance, volume, free):
        return {"series": f'windows_logical_disk_free_bytes{{k8s_cluster="{site}",instance="{instance}",volume="{volume}"}}',
                "values": f"{free}+0x30"}

    return [
        {"name": "a node container past 90% of its memory limit alerts; another node at the same figure does not", "interval": "1m",
         "input_series": [
             # 16.5 of 18 GiB: 92%.
             node(CAPACITY_NODE, int(16.5 * gib)),
             # A node with no limit of its own here.
             node("homelab-wsl-02-worker-2", int(16.5 * gib)),
         ], "alert_rule_test": [
             {"alertname": "NodeContainerMemoryNearLimit", "eval_time": "10m", "exp_alerts": []},
             {"alertname": "NodeContainerMemoryNearLimit", "eval_time": "20m", "exp_alerts": [
                 expected("NodeContainerMemoryNearLimit", node=CAPACITY_NODE)]},
         ]},
        {"name": "a node container at 89% of its memory limit does not alert", "interval": "1m",
         "input_series": [node(CAPACITY_NODE, int(16 * gib))],
         "alert_rule_test": [
             {"alertname": "NodeContainerMemoryNearLimit", "eval_time": "20m", "exp_alerts": []},
         ]},
        # 2 MB a minute is 2.88 GB a day, past twice the plan of 1.2 GB. The
        # streams with the default retention take in ten times that and don't count.
        {"name": "the 90-day streams above twice their plan alert, whatever the other streams take in", "interval": "1m",
         "input_series": [
             loki(SECURITY_RETENTION_HOURS, 2_000_000),
             loki(14 * 24, 20_000_000),
         ], "alert_rule_test": [
             {"alertname": "LokiSecurityIngestAbovePlan", "eval_time": "26h", "exp_alerts": [
                 expected("LokiSecurityIngestAbovePlan")]},
         ]},
        # 1.5 MB a minute is 2.16 GB a day: over the plan, under twice the plan.
        {"name": "the 90-day streams under twice their plan do not alert", "interval": "1m",
         "input_series": [
             loki(SECURITY_RETENTION_HOURS, 1_500_000),
             loki(14 * 24, 20_000_000),
         ], "alert_rule_test": [
             {"alertname": "LokiSecurityIngestAbovePlan", "eval_time": "26h", "exp_alerts": []},
         ]},
        {"name": "the drive under the WSL disk with 90 GB free alerts; another volume or host does not", "interval": "1m",
         "input_series": [
             drive(CAPACITY_DRIVE_HOST, CAPACITY_DRIVE_VOLUME, 90_000_000_000),
             drive(CAPACITY_DRIVE_HOST, "D:", 1_000_000_000),
             drive("other", CAPACITY_DRIVE_VOLUME, 1_000_000_000),
         ], "alert_rule_test": [
             {"alertname": "WslDriveSpaceLow", "eval_time": "20m", "exp_alerts": [
                 expected("WslDriveSpaceLow", instance=CAPACITY_DRIVE_HOST, volume=CAPACITY_DRIVE_VOLUME)]},
         ]},
        {"name": "the drive under the WSL disk with 110 GB free does not alert", "interval": "1m",
         "input_series": [drive(CAPACITY_DRIVE_HOST, CAPACITY_DRIVE_VOLUME, 110_000_000_000)],
         "alert_rule_test": [
             {"alertname": "WslDriveSpaceLow", "eval_time": "20m", "exp_alerts": []},
         ]},
    ]


def host_fixtures():
    def state(host, resource, value):
        return {"labels": f'homelab:host_state{{k8s_cluster="{host}",instance="{host}",resource="{resource}"}}',
                "value": value}

    def series(name, labels, values):
        return {"series": f'{name}{{k8s_cluster="one",instance="one",{labels}}}', "values": values}

    def node(name, labels, values, node="n1"):
        return {"series": f'{name}{{k8s_cluster="one",instance="{node}",{labels}}}', "values": values}

    return [
        {"name": "host states: half-busy CPU, RAM 93% in use, slow and nearly full disk, a host gone; its WSL VM short of memory and stalled on I/O", "interval": "1m",
         "input_series": [
             {"series": 'up{job="windows-exporter",k8s_cluster="one",instance="one"}', "values": "1+0x30"},
             # Two cores, each idle half of every second: 50% busy.
             series("windows_cpu_time_total", 'mode="idle",core="0"', "0+30x30"),
             series("windows_cpu_time_total", 'mode="idle",core="1"', "0+30x30"),
             # 93% of RAM in use. Commit charge isn't graded: promised past its
             # limit or not, it doesn't move the state.
             series("windows_memory_available_bytes", "", "7+0x30"),
             series("windows_memory_physical_total_bytes", "", "100+0x30"),
             series("windows_memory_committed_bytes", "", "99+0x30"),
             series("windows_memory_commit_limit", "", "100+0x30"),
             # C: does 10 operations a second at 100 ms each; a hidden partition is
             # full and slow, and doesn't count.
             series("windows_logical_disk_reads_total", 'volume="C:"', "0+600x30"),
             series("windows_logical_disk_writes_total", 'volume="C:"', "0+0x30"),
             series("windows_logical_disk_read_write_latency_seconds_total", 'volume="C:"', "0+60x30"),
             series("windows_logical_disk_free_bytes", 'volume="C:"', "3+0x30"),
             series("windows_logical_disk_size_bytes", 'volume="C:"', "100+0x30"),
             series("windows_logical_disk_reads_total", 'volume="HarddiskVolume1"', "0+600x30"),
             series("windows_logical_disk_writes_total", 'volume="HarddiskVolume1"', "0+0x30"),
             series("windows_logical_disk_read_write_latency_seconds_total", 'volume="HarddiskVolume1"', "0+6000x30"),
             series("windows_logical_disk_free_bytes", 'volume="HarddiskVolume1"', "0+0x30"),
             series("windows_logical_disk_size_bytes", 'volume="HarddiskVolume1"', "100+0x30"),
             # The other host's exporter is down: nothing to judge, so Unknown.
             {"series": 'up{job="windows-exporter",k8s_cluster="two",instance="two"}', "values": "0+0x30"},
             # This site's WSL VM, read from two nodes (the same kernel): one core
             # idle three quarters of the time (25% busy), 95% of memory in use,
             # half the disk free, and everything stalled on I/O 30% of the time.
             node("up", 'job="node-exporter"', "1+0x30"),
             node("up", 'job="node-exporter"', "1+0x30", "n2"),
             node("node_cpu_seconds_total", 'mode="idle",cpu="0"', "0+45x30"),
             node("node_cpu_seconds_total", 'mode="idle",cpu="0"', "0+45x30", "n2"),
             node("node_memory_MemAvailable_bytes", "", "5+0x30"),
             node("node_memory_MemTotal_bytes", "", "100+0x30"),
             node("node_filesystem_avail_bytes", 'mountpoint="/var"', "50+0x30"),
             node("node_filesystem_size_bytes", 'mountpoint="/var"', "100+0x30"),
             node("node_pressure_io_stalled_seconds_total", "", "0+18x30"),
         ], "promql_expr_test": [
             {"expr": "homelab:host_state", "eval_time": "20m", "exp_samples": [
                 state("one", "CPU", 1), state("one", "Memory", 2), state("one", "Disk latency", 2),
                 state("one", "Disk space", 3),
                 state("one", "WSL CPU", 1), state("one", "WSL memory", 2), state("one", "WSL disk space", 1),
                 state("one", "WSL I/O stall", 3),
                 state("two", "CPU", 0), state("two", "Memory", 0), state("two", "Disk latency", 0),
                 state("two", "Disk space", 0)]},
         ]},
        {"name": "host states: an idle disk isn't a slow one; a VM whose node-exporters are down is Unknown", "interval": "1m",
         "input_series": [
             {"series": 'up{job="windows-exporter",k8s_cluster="one",instance="one"}', "values": "1+0x30"},
             # A few slow reads a minute: under 1 operation a second, so not judged.
             series("windows_logical_disk_reads_total", 'volume="D:"', "0+3x30"),
             series("windows_logical_disk_writes_total", 'volume="D:"', "0+0x30"),
             series("windows_logical_disk_read_write_latency_seconds_total", 'volume="D:"', "0+3x30"),
             node("up", 'job="node-exporter"', "0+0x30"),
         ], "promql_expr_test": [
             {"expr": 'homelab:host_state{resource="Disk latency"}', "eval_time": "20m", "exp_samples": [
                 state("one", "Disk latency", 1)]},
             {"expr": 'homelab:host_state{resource=~"WSL.*"}', "eval_time": "20m", "exp_samples": [
                 state("one", "WSL CPU", 0), state("one", "WSL memory", 0), state("one", "WSL disk space", 0),
                 state("one", "WSL I/O stall", 0)]},
         ]},
    ]


def house_fixtures():
    own = {"series": 'homelab_observer_info{k8s_cluster="one",observer_cluster="one"}', "values": "1+0x10"}

    def state(domain, value):
        return {"labels": f'homelab:house_state{{domain="{domain}"}}', "value": value}

    service = 'homelab_expected_workload_info{k8s_cluster="%s",app="%s"}'
    return [
        {"name": "household view: this site's share, worst app, partial cameras", "interval": "1m",
         "input_series": [
             own,
             {"series": 'probe_success{k8s_cluster="one",observability_target="internet"}', "values": "1+0x10"},
             {"series": 'homelab_observer_info{k8s_cluster="one",observer_cluster="one",job="x"}', "values": "1+0x10"},
             # Home Assistant runs here; a failed app here; a standby app; the
             # other site's apps (Unknown from here) don't count.
             {"series": 'probe_success{k8s_cluster="one",observability_target="service",app="home-assistant"}', "values": "1+0x10"},
             {"series": service % ("one", "home-assistant"), "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",observability_target="service",app="broken"}', "values": "0+0x10"},
             {"series": service % ("one", "broken"), "values": "1+0x10"},
             {"series": service % ("two", "elsewhere"), "values": "1+0x10"},
             # Three cameras worked today, two do now, one has been off all along.
             {"series": 'frigate_camera_fps{camera_name="a"}', "values": "5+0x10"},
             {"series": 'frigate_camera_fps{camera_name="b"}', "values": "5+0x10"},
             {"series": 'frigate_camera_fps{camera_name="c"}', "values": "5 5 0 0 0 0 0 0 0 0 0"},
             {"series": 'frigate_camera_fps{camera_name="off"}', "values": "0+0x10"},
             # Every AP that was up is up.
             {"series": 'unpoller_device_uptime_seconds{name="ap1"}', "values": "100+60x10"},
             {"series": 'unpoller_device_uptime_seconds{name="ap2",source_site="two"}', "values": "100+60x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "0+60x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:house_state", "eval_time": "5m", "exp_samples": [
                 state("apps", 3), state("automation", 1), state("cameras", 2),
                 state("internet", 1), state("monitoring", 1), state("wifi", 1)]},
         ]},
        {"name": "household view: nothing working, and no part in a domain", "interval": "1m",
         "input_series": [
             own,
             {"series": 'probe_success{k8s_cluster="one",observability_target="internet"}', "values": "0+0x10"},
             {"series": 'frigate_camera_fps{camera_name="a"}', "values": "5 5 0 0 0 0 0 0 0 0 0"},
             # The AP is reported, and down.
             {"series": 'unpoller_device_uptime_seconds{name="ap1"}', "values": "100 160 0 0 0 0 0 0 0 0 0"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "0+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             # No apps here: nothing failing. No Home Assistant here: no row.
             {"expr": "homelab:house_state", "eval_time": "10m", "exp_samples": [
                 state("apps", 1), state("cameras", 3), state("internet", 3), state("monitoring", 3),
                 state("wifi", 3)]},
         ]},
        {"name": "household view: backups, smart devices, storage and away access", "interval": "1m",
         "input_series": [
             own,
             # One app's recovery point is degraded; databases fine; a replica's N/A ignored.
             {"series": 'homelab:app_rpo_state{k8s_cluster="one",app="a"}', "values": "1+0x10"},
             {"series": 'homelab:app_rpo_state{k8s_cluster="one",app="b"}', "values": "2+0x10"},
             {"series": 'homelab:database_backup_state{k8s_cluster="one",db_cluster="d"}', "values": "1+0x10"},
             {"series": 'homelab:database_backup_state{k8s_cluster="one",db_cluster="r"}', "values": "5+0x10"},
             # The other site's failing backup isn't this site's share.
             {"series": 'homelab:app_rpo_state{k8s_cluster="two",app="c"}', "values": "3+0x10"},
             # 20 devices up today; one drops (95%: fine). An automation going away isn't a device.
             *[{"series": f'homeassistant_entity_available{{domain="sensor",entity="sensor.d{i}"}}', "values": "1+0x10"}
               for i in range(19)],
             {"series": 'homeassistant_entity_available{domain="light",entity="light.d19"}', "values": "1 1 0 0 0 0 0 0 0 0 0"},
             {"series": 'homeassistant_entity_available{domain="automation",entity="automation.x"}', "values": "1 1 0 0 0 0 0 0 0 0 0"},
             # The fuller drive is 8% free: degraded.
             {"series": 'windows_logical_disk_free_bytes{k8s_cluster="one",volume="C:"}', "values": "50+0x10"},
             {"series": 'windows_logical_disk_size_bytes{k8s_cluster="one",volume="C:"}', "values": "100+0x10"},
             {"series": 'windows_logical_disk_free_bytes{k8s_cluster="one",volume="E:"}', "values": "8+0x10"},
             {"series": 'windows_logical_disk_size_bytes{k8s_cluster="one",volume="E:"}', "values": "100+0x10"},
             {"series": 'cloudflared_tunnel_ha_connections{k8s_cluster="one"}', "values": "4+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain=~"backups|devices|storage|away"}', "eval_time": "10m", "exp_samples": [
                 state("away", 1), state("backups", 2), state("devices", 1), state("storage", 2)]},
         ]},
        {"name": "household view: backups, devices, storage and away access failing", "interval": "1m",
         "input_series": [
             own,
             {"series": 'homelab:app_rpo_state{k8s_cluster="one",app="a"}', "values": "3+0x10"},
             # Half the devices are gone, then all of them.
             *[{"series": f'homeassistant_entity_available{{domain="switch",entity="switch.d{i}"}}',
                "values": "1 1 0 0 0 0 0 0 0 0 0"} for i in range(4)],
             {"series": 'windows_logical_disk_free_bytes{k8s_cluster="one",volume="C:"}', "values": "3+0x10"},
             {"series": 'windows_logical_disk_size_bytes{k8s_cluster="one",volume="C:"}', "values": "100+0x10"},
             {"series": 'cloudflared_tunnel_ha_connections{k8s_cluster="one"}', "values": "4 4 2 0 0 0 0 0 0 0 0"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain=~"backups|devices|storage|away"}', "eval_time": "10m", "exp_samples": [
                 state("away", 3), state("backups", 3), state("devices", 3), state("storage", 3)]},
         ]},
        {"name": "household view: no part in the new domains", "interval": "1m",
         "input_series": [
             own,
             {"series": 'homelab:app_rpo_state{k8s_cluster="two",app="c"}', "values": "3+0x10"},
             {"series": 'cloudflared_tunnel_ha_connections{k8s_cluster="two"}', "values": "4+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain=~"backups|devices|storage|away"}', "eval_time": "10m", "exp_samples": []},
         ]},
        {"name": "household view: a Frigate pod with a new IP is the same cameras", "interval": "1m",
         "input_series": [
             own,
             {"series": 'frigate_camera_fps{camera_name="a",instance="10.0.0.1:5000"}', "values": "5 5 _ _ _ _ _ _ _ _ _"},
             {"series": 'frigate_camera_fps{camera_name="b",instance="10.0.0.1:5000"}', "values": "5 5 _ _ _ _ _ _ _ _ _"},
             {"series": 'frigate_camera_fps{camera_name="a",instance="10.0.0.2:5000"}', "values": "_ _ 5 5 5 5 5 5 5 5 5"},
             {"series": 'frigate_camera_fps{camera_name="b",instance="10.0.0.2:5000"}', "values": "_ _ 5 5 5 5 5 5 5 5 5"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain="cameras"}', "eval_time": "10m", "exp_samples": [state("cameras", 1)]},
         ]},
        {"name": "household view: a camera added today is one more camera, not a gap", "interval": "1m",
         "input_series": [
             own,
             {"series": 'frigate_camera_fps{camera_name="a",instance="10.0.0.1:5000"}', "values": "5 5 5 _ _ _ _ _ _ _ _"},
             # Frigate restarts with the new camera, on a new IP; it streams once
             # it's set up.
             {"series": 'frigate_camera_fps{camera_name="a",instance="10.0.0.2:5000"}', "values": "_ _ _ 5 5 5 5 5 5 5 5"},
             {"series": 'frigate_camera_fps{camera_name="new",instance="10.0.0.2:5000"}', "values": "_ _ _ 0 0 5 5 5 5 5 5"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain="cameras"}', "eval_time": "4m", "exp_samples": [state("cameras", 1)]},
             {"expr": 'homelab:house_state{domain="cameras"}', "eval_time": "10m", "exp_samples": [state("cameras", 1)]},
         ]},
        {"name": "household view: a blind source is Unknown, not Failed", "interval": "1m",
         "input_series": [
             own,
             # Both exporters stop reporting: the site that runs UniFi Poller is
             # lost, Frigate's scrape fails. What they watch may be fine.
             {"series": 'frigate_camera_fps{camera_name="a"}', "values": "5 5 _ _ _ _ _ _ _ _ _"},
             {"series": 'unpoller_device_uptime_seconds{name="ap1",source_site="two"}', "values": "100 160 _ _ _ _ _ _ _ _ _"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain=~"wifi|cameras"}', "eval_time": "10m", "exp_samples": []},
         ]},
    ]


def other_database_fixtures():
    info = 'homelab_expected_other_database_info{k8s_cluster="one",observer_cluster="one",namespace="db",workload="%s",kind="%s",engine="e",app="a"}'
    return [
        {"name": "other databases: ready, failed, standby, unobserved", "interval": "1m",
         "input_series": [
             {"series": info % ("ok", "statefulset"), "values": "1+0x10"},
             {"series": 'kube_statefulset_replicas{k8s_cluster="one",namespace="db",statefulset="ok"}', "values": "1+0x10"},
             {"series": 'kube_statefulset_status_replicas_ready{k8s_cluster="one",namespace="db",statefulset="ok"}', "values": "1+0x10"},
             {"series": info % ("down", "deployment"), "values": "1+0x10"},
             {"series": 'kube_deployment_spec_replicas{k8s_cluster="one",namespace="db",deployment="down"}', "values": "1+0x10"},
             {"series": 'kube_deployment_status_replicas_available{k8s_cluster="one",namespace="db",deployment="down"}', "values": "0+0x10"},
             {"series": info % ("standby", "statefulset"), "values": "1+0x10"},
             {"series": 'kube_statefulset_replicas{k8s_cluster="one",namespace="db",statefulset="standby"}', "values": "0+0x10"},
             {"series": 'kube_statefulset_status_replicas_ready{k8s_cluster="one",namespace="db",statefulset="standby"}', "values": "0+0x10"},
             {"series": info % ("missing", "statefulset"), "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:other_database_state", "eval_time": "5m", "exp_samples": [
                 {"labels": f'homelab:other_database_state{{k8s_cluster="one",namespace="db",workload="{w}",engine="e",app="a"}}', "value": v}
                 for w, v in (("down", 3), ("missing", 0), ("ok", 1), ("standby", 4))]},
         ]},
    ]


def objective_fixtures():
    sched = 'homelab_app_backup_scheduled{k8s_cluster="one",observer_cluster="one",namespace="apps",app="%s",pvc="%s",policy="%s"}'
    kopiur = 'kopiur_policy_last_backup_success_timestamp_seconds{k8s_cluster="one",namespace="apps",policy="%s"}'
    objective = 'homelab_app_recovery_objective_seconds{k8s_cluster="one",observer_cluster="one",app="%s",objective="%s"}'
    return [
        {"name": "an app's backup age is its oldest piece, and a piece without evidence is Unknown", "interval": "1m",
         "input_series": [
             # fresh: one PVC 100 s old and a primary database 200 s old.
             {"series": sched % ("fresh", "data", "fresh"), "values": "1+0x10"},
             {"series": kopiur % "fresh", "values": "200+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="fresh-pg",app="fresh"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="fresh-pg-1"}', "values": "0+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="fresh-pg-1"}', "values": "100+0x10"},
             {"series": objective % ("fresh", "rpo"), "values": "1000+0x10"},
             # stale: past its objective.
             {"series": sched % ("stale", "data", "stale"), "values": "1+0x10"},
             {"series": kopiur % "stale", "values": "0+0x10"},
             {"series": objective % ("stale", "rpo"), "values": "60+0x10"},
             # partial: one of two scheduled PVCs has never backed up.
             {"series": sched % ("partial", "a", "partial-a"), "values": "1+0x10"},
             {"series": sched % ("partial", "b", "partial-b"), "values": "1+0x10"},
             {"series": kopiur % "partial-a", "values": "250+0x10"},
             {"series": objective % ("partial", "rpo"), "values": "1000+0x10"},
             # standby: suspended here, so no row at all.
             {"series": sched % ("standby", "data", "standby"), "values": "0+0x10"},
             {"series": objective % ("standby", "rpo"), "values": "1000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_backup_age_seconds", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="fresh"}', "value": 200},
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="partial"}', "value": 50},
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="stale"}', "value": 300}]},
             {"expr": "homelab:app_rpo_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="fresh"}', "value": 1},
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="partial"}', "value": 0},
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="stale"}', "value": 3}]},
         ]},
        {"name": "a primary without backup evidence is Unknown; a replica's missing backup isn't counted", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg",app="app"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "0+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="app-pg",app="app"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="two",namespace="databases",pod="app-pg-1"}', "values": "1+0x10"},
             {"series": 'homelab_app_recovery_objective_seconds{k8s_cluster="one",app="app",objective="rpo"}', "values": "1000+0x10"},
             {"series": 'homelab_app_recovery_objective_seconds{k8s_cluster="two",app="app",objective="rpo"}', "values": "1000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_rpo_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="app"}', "value": 0}]},
         ]},
        {"name": "restore time: within, past, Unknown without a drill, Not applicable where the app runs", "interval": "1m",
         "input_series": [
             {"series": 'homelab_restore_last_duration_seconds{k8s_cluster="one",observer_cluster="one",app="quick"}', "values": "30+0x10"},
             {"series": objective % ("quick", "rto"), "values": "3600+0x10"},
             {"series": 'homelab_restore_last_duration_seconds{k8s_cluster="one",observer_cluster="one",app="slow"}', "values": "7200+0x10"},
             {"series": objective % ("slow", "rto"), "values": "3600+0x10"},
             {"series": objective % ("untested", "rto"), "values": "3600+0x10"},
             {"series": objective % ("active", "rto"), "values": "3600+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="active",kind="deployment",workload="active"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="untested",kind="deployment",workload="untested"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_rto_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="quick"}', "value": 1},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="slow"}', "value": 3},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="untested"}', "value": 0},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="active"}', "value": 5}]},
         ]},
    ]


def generate():
    resource = rule_resource()
    result = {
        ROOT / "kubernetes/apps/monitoring/observability-evidence/rules/rules.yaml": resource,
        ROOT / "observability/generated/rules-for-test.yaml": {"groups": resource["spec"]["groups"]},
        ROOT / "observability/generated/rule-tests.yaml": fixtures(),
    }
    for path, data in result.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + yaml.safe_dump(data, sort_keys=False, width=110))


if __name__ == "__main__":
    generate()
