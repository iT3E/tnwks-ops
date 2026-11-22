"""Generate normalized evidence rules and executable PromQL behavior fixtures."""

from pathlib import Path

import yaml

from inventory import AI_GOOD_RESULTS, AI_RESTORE_CHECKS, AI_RESULT_WINDOWS, AI_SCHEDULES, inventory

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
        **ai_records(),
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


def ai_records():
    """The AI domain (inventory.py AI_*, docs/apps/agent-platform.md): one state
    for each declared part, scheduled check, result line and backup. A row whose
    source is gone stays, as Unknown (0); a part a site does not run is Not
    applicable (5) there."""
    part = "k8s_cluster, trust_domain, part, namespace, kind, workload"
    key = "k8s_cluster, namespace, workload"
    expected = f"max by ({part}) (homelab_expected_ai_part_info)"
    absent = f"max by ({part}) (homelab_absent_ai_part_info)"

    def per_workload(sts, deploy):
        return (f'max by ({key}) (label_replace({sts}, "workload", "$1", "statefulset", "(.+)")'
                f' or label_replace({deploy}, "workload", "$1", "deployment", "(.+)"))')

    desired = per_workload("kube_statefulset_replicas", "kube_deployment_spec_replicas")
    ready = per_workload("kube_statefulset_status_replicas_ready", "kube_deployment_status_replicas_available")

    # A scheduled check: its last good run against its window (the objective).
    # The namespaces are the ones of the declared schedules (inventory.py
    # AI_SCHEDULES), so a schedule in a new namespace is matched too: a fixed
    # list left litellm-key-check (namespace ai) at no evidence.
    job = "k8s_cluster, namespace, cronjob"
    scope = f'namespace=~"{"|".join(sorted({item["namespace"] for item in AI_SCHEDULES}))}"'
    schedule = "max by (k8s_cluster, trust_domain, namespace, cronjob) (homelab_ai_schedule_objective_seconds)"
    success = f"max by ({job}) (kube_cronjob_status_last_successful_time{{{scope}}})"
    # The start of the newest failed Job of each CronJob (AgentPlatformCronJobFailed).
    failed = (f"max by ({job}) (kube_job_status_start_time{{{scope}}}"
              f" * on (k8s_cluster, namespace, job_name) group_left (cronjob)"
              f' max by (k8s_cluster, namespace, job_name, cronjob) (label_replace(kube_job_owner{{{scope},owner_kind="CronJob"}},'
              f' "cronjob", "$1", "owner_name", "(.+)"))'
              f' and on (k8s_cluster, namespace, job_name) (kube_job_status_failed{{{scope},reason!=""}} > 0))')
    left = f"({schedule} - on ({job}) group_left () (time() - {success}))"

    # A result line: the newest good line against the newest other one, in its window.
    result = "k8s_cluster, trust_domain, check"
    expected_result = f"max by ({result}) (homelab_expected_ai_result_info)"
    good = "|".join(AI_GOOD_RESULTS)

    def lines(match):
        return " or ".join(
            f'max by ({result}) (label_replace(max_over_time(homelab_agent_platform_result_timestamp_seconds'
            f'{{pod_namespace=~"agents-.+",check="{check}",result{match}"{good}"}}[{window}]),'
            f' "trust_domain", "$1", "pod_namespace", "agents-(.+)"))'
            for check, window in AI_RESULT_WINDOWS.items())

    newest_good, newest_bad = f"({lines('=~')})", f"({lines('!~')})"

    backup = "k8s_cluster, trust_domain, namespace, policy"
    policy = "k8s_cluster, namespace, policy"
    objective = f"max by ({backup}) (homelab_ai_backup_objective_seconds)"
    restore = "|".join(AI_RESTORE_CHECKS)
    return {
        # 5 absent by design, 4 scaled to zero, 1 every replica ready, 2 some,
        # 3 none, 0 no kube-state-metrics series (the workload is gone).
        "homelab:ai_part_state": (
            f"({absent} * 0 + 5)"
            f" or ({expected} * 0 + 4) and on ({key}) ({desired} == 0)"
            f" or ({expected} * 0 + 1) and on ({key}) (({ready} >= {desired}) > 0)"
            f" or ({expected} * 0 + 2) and on ({key}) (({ready} < {desired}) and on ({key}) ({ready} > 0))"
            f" or ({expected} * 0 + 3) and on ({key}) ({ready} < {desired})"
            f" or ({expected} * 0)"),
        # 4 suspended, 3 the newest run failed after the last good one (or no
        # run was good), 1 a good run in its window, 2 the last good run is
        # older, 0 no evidence.
        "homelab:ai_schedule_state": (
            f"({schedule} * 0 + 4) and on ({job}) (max by ({job}) (kube_cronjob_spec_suspend{{{scope}}}) == 1)"
            f" or ({schedule} * 0 + 3) and on ({job}) (({failed} > {success}) or ({failed} unless on ({job}) {success}))"
            f" or (({left} >= 0) * 0 + 1)"
            f" or (({left} < 0) * 0 + 2)"
            f" or ({schedule} * 0)"),
        # 3 the newest line in the window is not good, 1 it is, 0 no line.
        "homelab:ai_result_state": (
            f"({expected_result} * 0 + 3) and on ({result}) (({newest_bad} > {newest_good})"
            f" or ({newest_bad} unless on ({result}) {newest_good}))"
            f" or ({expected_result} * 0 + 1) and on ({result}) {newest_good}"
            f" or ({expected_result} * 0)"),
        "homelab:ai_backup_age_seconds": (
            f"((time() - max by ({policy}) (kopiur_policy_last_backup_success_timestamp_seconds)) >= 0)"
            f" * on ({policy}) group_right () (0 * {objective} + 1)"),
        # 1 within the recovery point objective, 3 past it, 0 no backup evidence.
        "homelab:ai_backup_state": (
            f"((homelab:ai_backup_age_seconds <= bool on ({backup}) {objective})"
            f" + 3 * (homelab:ai_backup_age_seconds > bool on ({backup}) {objective}))"
            f" or ({objective} * 0)"),
        # A restore test runs by hand, now and then, and its result line lives
        # 5 minutes as a series. Its samples stay in the local history (15
        # days), so this keeps the time of the newest line of each result for
        # that long. (A rule that read its own last value would keep it longer,
        # but contracts.py refuses a rule that reads itself.)
        "homelab:ai_restore_result_timestamp_seconds": (
            f"max by (k8s_cluster, trust_domain, check, result) (label_replace(max_over_time("
            f'homelab_agent_platform_result_timestamp_seconds{{pod_namespace=~"agents-.+",check=~"{restore}"}}[15d]),'
            f' "trust_domain", "$1", "pod_namespace", "agents-(.+)"))'),
        **plan_records(),
    }


# The use of the Claude plan (docs/apps/agent-platform.md, "Plan use"). The
# model bridge logs a plan_limit line for a window only when its use, status or
# reset moves, and the gauges of Vector expire one hour after their last line.
# So each record reads the newest reading of the last 8 days (one week window
# and a day). The plan is one account: the gauges carry the window only, and a
# restart of the Vector pod gives a series of a new pod, so the newest reading
# of all pods wins.
PLAN_HELD = "8d"
# A reading with no reset time counts for 6 hours (more than the five hour window).
PLAN_UNKNOWN_RESET_SECONDS = 6 * 3600


def plan_records():
    window = "k8s_cluster, window"
    pod = "k8s_cluster, window, namespace, pod"
    observed = f"last_over_time(homelab_model_bridge_plan_observed_timestamp_seconds[{PLAN_HELD}])"

    def newest(name):
        return (f"max by ({window}) (last_over_time({name}[{PLAN_HELD}]) and on ({pod})"
                f" ({observed} == on ({window}) group_left () homelab:ai_plan_observed_timestamp_seconds))")

    reset = newest("homelab_model_bridge_plan_reset_timestamp_seconds")
    # A reading counts until its window resets, or for 6 hours when the line gave
    # no reset time. After that the window is new, and its use is not known.
    current = (f"(({reset}) > time()) or ((({reset}) <= 0) and on ({window})"
               f" ((time() - homelab:ai_plan_observed_timestamp_seconds) < {PLAN_UNKNOWN_RESET_SECONDS}))")
    return {
        "homelab:ai_plan_observed_timestamp_seconds": f"max by ({window}) ({observed})",
        # The share used of the window (1 is the limit), while the reading counts.
        "homelab:ai_plan_utilization_ratio": (
            f"({newest('homelab_model_bridge_plan_utilization_ratio')} >= 0) and on ({window}) ({current})"),
        "homelab:ai_plan_reset_timestamp_seconds": f"{reset} > time()",
        # The status as a state: 1 allowed, 2 allowed_warning (near the limit),
        # 3 rejected (at the limit), 0 a status that the bridge did not know.
        "homelab:ai_plan_state": (
            f"clamp_min({newest('homelab_model_bridge_plan_status')} + 1, 0) and on ({window}) ({current})"),
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
# The node containers of homelab-wsl and their Docker memory limits, in GiB:
# memory_workers and memory_controlplanes of talos_clusters.wsl in the Ansible
# inventory (that cluster sets no memory_nodes, so both workers have the same
# limit). No series carries a limit, so it is stated again here, and
# observability/tests/test_node_container_memory.py holds it equal to the
# inventory. The kubelet of a node reports the memory of the whole WSL VM, so
# neither the scheduler nor the evictions of the kubelet see these limits.
SITE1_CLUSTER = "homelab-wsl"
SITE1_WORKER_LIMIT_GIB = 13
SITE1_CONTROLPLANE_LIMIT_GIB = 4


# The section of each alert of the security platform in its runbook. A plain
# link: no variable of the alert, so the link names no check, finding or value.
SECURITY_RUNBOOK = "https://github.com/iT3E/tnwks-ops/blob/main/docs/apps/security-platform-runbook.md"


def security_runbook(alert):
    return f"{SECURITY_RUNBOOK}#{alert.lower()}"


def capacity_alerts():
    """Each alert reads a series that only one site's Prometheus has, so the rule
    is quiet on the other site: the kubelet's node figure of each site is
    scraped on that site (each rule names its nodes), Loki's own series on
    homelab-wsl-02, and the Windows hosts by homelab-wsl."""
    def board(uid):
        return f'https://grafana.${{INTERNAL_DOMAIN}}/d/{uid}?var-cluster={{{{ $labels.k8s_cluster }}}}'

    def site1_memory(role, limit_gib, share):
        """The working set of the node containers of homelab-wsl of one role
        past a share of their limit. A node name of Talos in Docker ends in
        -worker-<n> or -controlplane-<n>."""
        return (f'max by (k8s_cluster, node) (node_memory_working_set_bytes{{k8s_cluster="{SITE1_CLUSTER}",node=~".+-{role}-[0-9]+"}})'
                f' > {share} * {limit_gib} * 1024^3')

    steps = " docs/operations/observability-operations.md, Room on site 1, has the steps."
    worker_limit = (f"The node container of a worker of {SITE1_CLUSTER} has a memory limit of {SITE1_WORKER_LIMIT_GIB} GiB"
                    " (memory_workers of talos_clusters in the Ansible inventory). The scheduler and the evictions of the"
                    " kubelet do not see it, because the node reports the memory of the whole WSL VM: the scheduler places"
                    " pods against the memory of the VM. This alert warns; it stops nothing.")

    return [
        # The working set of the node's root cgroup, which is the node container
        # (a private cgroup namespace): what the kubelet reports for its node.
        {"alert": "NodeContainerMemoryNearLimit",
         "expr": (f'max by (k8s_cluster, node) (node_memory_working_set_bytes{{node="{CAPACITY_NODE}"}})'
                  f' > 0.9 * {CAPACITY_NODE_LIMIT_GIB} * 1024^3'),
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": "{{ $labels.node }} is above 90% of the memory limit of its node container",
                         "dashboard_url": board("tnwks-kubernetes"),
                         "runbook_url": security_runbook("NodeContainerMemoryNearLimit"),
                         "description": (f"The node container has a memory limit of {CAPACITY_NODE_LIMIT_GIB} GiB. The scheduler does not see it,"
                                         " because the node reports the memory of the whole WSL VM. Find the largest pods of the node,"
                                         " and move or shrink one. The limit is memory_nodes of talos_clusters in the Ansible inventory.")}},
        # The same figure for the nodes of homelab-wsl, scraped there
        # (kubernetes/clusters/wsl/apps). A worker can fill in minutes (a
        # rollout, or Jobs that all land on it), so its rules wait less. One
        # name with two severities: Alertmanager's inhibit rule holds the
        # warning back while the critical one fires. It does not compare the
        # node, so it holds the warning of the other worker back too.
        {"alert": "WorkerContainerMemoryNearLimit",
         "expr": site1_memory("worker", SITE1_WORKER_LIMIT_GIB, 0.9),
         "for": "5m", "labels": {"severity": "warning"},
         "annotations": {"summary": "{{ $labels.node }} is above 90% of the memory limit of its node container",
                         "dashboard_url": board("tnwks-kubernetes"),
                         "description": (worker_limit + " Find the largest pods of the node, and move or shrink one." + steps)}},
        {"alert": "WorkerContainerMemoryNearLimit",
         "expr": site1_memory("worker", SITE1_WORKER_LIMIT_GIB, 0.95),
         "for": "2m", "labels": {"severity": "critical"},
         "annotations": {"summary": "{{ $labels.node }} is above 95% of the memory limit of its node container",
                         "dashboard_url": board("tnwks-kubernetes"),
                         "description": (worker_limit + " At the limit, the kernel kills processes inside the node container,"
                                         " BestEffort pods first, and the kubelet does not evict before. Move a large pod off"
                                         " the node now." + steps)}},
        # The control plane runs no workload pods (its taint), so its memory
        # moves slowly. Its working set counts active file pages, such as the
        # database file of etcd, which the kernel can take back, and it sits
        # well past 90% while its anonymous memory does not. At 98% for 15
        # minutes the kernel has almost nothing left to take back.
        {"alert": "ControlPlaneContainerMemoryNearLimit",
         "expr": site1_memory("controlplane", SITE1_CONTROLPLANE_LIMIT_GIB, 0.98),
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": "{{ $labels.node }} is above 98% of the memory limit of its node container",
                         "dashboard_url": board("tnwks-kubernetes"),
                         "description": (f"The node container of the control plane of {SITE1_CLUSTER} has a memory limit of"
                                         f" {SITE1_CONTROLPLANE_LIMIT_GIB} GiB (memory_controlplanes of talos_clusters in the"
                                         " Ansible inventory). It runs the API server and etcd. Its working set also counts"
                                         " active file pages, such as the database file of etcd, which the kernel can take back."
                                         " At 98% little is left to take back: compare rssBytes in the summary of the kubelet,"
                                         " and find what grew." + steps)}},
        {"alert": "LokiSecurityIngestAbovePlan",
         "expr": ('sum by (k8s_cluster) (increase(loki_distributor_bytes_received_total'
                  f'{{retention_hours="{SECURITY_RETENTION_HOURS}"}}[1d])) > 2 * {SECURITY_INGEST_PLAN_GB}e9'),
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "Loki on {{ $labels.k8s_cluster }} took in more than twice the planned bytes of its 90-day streams in a day",
                         "dashboard_url": board("tnwks-observability"),
                         "runbook_url": security_runbook("LokiSecurityIngestAbovePlan"),
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
# The API token of the Prowler run for Cloudflare, and of a later run of the kind
# (kubernetes/apps/security/prowler). Nothing renews a token, so the owner
# writes its end date to the renew time of its Lease when the token is made,
# in the form of the certificates above.
SECURITY_TOKEN_LEASES = "prowler-.+-token"


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

# The source `trivy` of the security registry (security/README.md, "The
# series"): Trivy Operator on each cluster (kubernetes/apps/security/trivy-operator).
# The rules below run on each site, for the series of that site only, and the
# federation brings the two series of homelab-wsl to homelab-wsl-02.
TRIVY_COMPLIANCE_SPEC = "k8s-cis-1.23"
TRIVY_COMPLIANCE_TITLE = "CIS Kubernetes Benchmarks v1.23"
# The controls that have no input on these clusters. The node collector is off
# (1.1.x, 4.1.x, 4.2.x read the files and the settings of a node), and Talos
# runs etcd as a service of the OS and not as a pod (2.x). Trivy counts a
# control with no input as a pass, so these stay out: "no evidence", never a
# pass. The README of trivy-operator says why the node collector is off.
TRIVY_CONTROLS_WITH_NO_INPUT = r"1\.1\..+|2\..+|4\.[12]\..+"
TRIVY_SEVERITIES = ("critical", "high", "medium", "low", "unknown")
# The pods of the scan jobs of Trivy Operator. The operator does not scan its
# own jobs, so their image is not a running image that needs a report.
TRIVY_SCAN_POD = "scan-vulnerabilityreport-.+"
# An image whose report is gone for less than this still counts as scanned,
# and its vulnerabilities still count: the operator deletes a report at its end
# (24h) and then scans the image again.
TRIVY_REPORT_GAP = "1h"
# How long a running image may have no report before SecurityImagesNotScanned
# fires: the late_after of the source `trivy` (security/registry/sources.yaml).
TRIVY_UNSCANNED_HOLD = "36h"
# How long the rule keeps the last value of the accepted list when its series
# stops: the list is served on homelab-wsl-02 only, and homelab-wsl reads it
# through the federation. An entry taken out of the list early stays muted for
# up to this long.
TRIVY_ACCEPTED_HOLD = "1d"
# The check of an entry of the accepted list that mutes SecurityImagesNotScanned
# for its cluster (a private image that no scan can read). It is not a check of
# the registry: it names the alert, not a result.
TRIVY_UNSCANNED_CHECK = "images-not-scanned"
# A new finding of high severity (design table "The cycle of each check"). The
# alert reads high and critical: the severities that the design asks to alert
# on. The others are on the board only. The alert holds for one hour.
SECURITY_FINDING_SEVERITY = "high|critical"
SECURITY_FINDING_HOLD = "1h"
# One value for each check: no pod, instance, container or endpoint (they change
# when the pod that serves a result starts again), and no severity (a check
# whose severity changes is not a new finding). standby: a series that the other
# site scrapes for itself (SECURITY_STANDBY_JOB), kept so the alert can tell it.
SECURITY_FINDING_LABELS = "k8s_cluster, source, check, system, standby"
# The host check of both Windows hosts (Design Doc 0004, section 5.5). homelab-wsl-02
# scrapes their security series in this job, and homelab-wsl scrapes them in the
# job windows-exporter with the label standby="true". homelab-wsl federates the
# `up` of this job from homelab-wsl-02: while that `up` is 1 for a host, site 2 has
# the results of that host, and the standby series of the host do not alert here.
SECURITY_STANDBY_JOB = "windows-exporter-security"
SECURITY_FINDING_RECORD = "homelab:security_check_result_high:max"


def security_selector(metric, **labels):
    """A selector of this site's own series: a federated copy has source_site."""
    matchers = ['source_site=""'] + [f'{key}{value}' for key, value in labels.items()]
    return f'{metric}{{{",".join(matchers)}}}'


# The steps that turn the image of a pod spec into the reference of a report of
# Trivy Operator: <registry>/<repository>:<tag>. The operator scans the image of
# the pod spec and writes the registry, the repository and the tag that
# go-containerregistry parses from it (pkg/plugins/trivy/plugin.go, ParseImageRef,
# at v0.36.1). That parser (pkg/name, v0.22.1) takes the first part as the registry
# only if it is localhost or has a "." or a ":", puts index.docker.io in place of
# no registry or docker.io, adds library/ to a single name on Docker Hub, and
# adds the tag latest when there is none. A reference with only a digest gives no
# tag in the report, so it gets no reference here: only its digest joins.
# Each step is (regex of the whole label, replacement); the label keeps its value
# when the regex does not match. A regex has no backslash: "[.]" is a dot.
TRIVY_IMAGE_REF_STEPS = (
    # No tag and no digest: the tag latest.
    ("([^@]*/)?([^/:@]+)", "$1$2:latest"),
    # A tag and a digest: the tag only. The operator writes the tag of such a
    # reference as the rest of the pod-spec string (postgres:16@sha256:... gives
    # the tag "postgres:16"), so this reference never matches its report: these
    # images join by their digest, which the report takes from the reference.
    ("([^@]+:[^@/]+)@.+", "$1"),
    # A digest only: no reference.
    ("[^@]*@.*", ""),
    # One name: Docker Hub, in the namespace library.
    ("([^/]+)", "index.docker.io/library/$1"),
    # A first part that is not a registry: Docker Hub.
    ("([^/.:]+/.+)", "index.docker.io/$1"),
    ("index[.]docker[.]io/(localhost/.+)", "$1"),
    ("docker[.]io/(.+)", "index.docker.io/$1"),
    ("index[.]docker[.]io/([^/]+)", "index.docker.io/library/$1"),
)


def trivy_running_containers():
    """One series for each container that runs on this site now, with the digest of
    its image (`digest`) and the reference that a report of its image has
    (`image_ref`, TRIVY_IMAGE_REF_STEPS). A container that has not started yet has
    an empty image_id, and an image with no digest of a registry (loaded on the
    node) can not have a report: neither counts. The pods of the scan jobs do not
    count either: the operator does not scan its own jobs."""
    not_a_scan = f'!~"{TRIVY_SCAN_POD}"'
    with_digest = '=~".+@sha256:.+"'
    parts = []
    for metric in ("kube_pod_container_info", "kube_pod_init_container_info"):
        expr = (f'label_replace({security_selector(metric, pod=not_a_scan, image_id=with_digest)},'
                ' "digest", "$1", "image_id", ".*@(sha256:.+)")')
        expr = f'label_replace({expr}, "image_ref", "$1", "image_spec", "(.+)")'
        for regex, replacement in TRIVY_IMAGE_REF_STEPS:
            expr = f'label_replace({expr}, "image_ref", "{replacement}", "image_ref", "{regex}")'
        parts.append(expr)
    return " or ".join(parts)


# The parts of the Trivy rules that more than one rule reads are recording rules
# of their own, evaluated first in the group: each rule reads the recorded series
# in place of the whole expression again, so an evaluation computes each part once.
# The running containers (trivy_running_containers()).
TRIVY_RUNNING_RECORD = "homelab:security_trivy_running_containers:info"
# The reports of the last TRIVY_REPORT_GAP (trivy_reports_window()).
TRIVY_REPORTS_RECORD = "homelab:security_trivy_reports:max"
# The reports of the images that run now (trivy_reports_of_running()).
TRIVY_REPORTS_OF_RUNNING_RECORD = "homelab:security_trivy_reports_of_running:max"
# The digests of the running images with no report (trivy_unscanned_images()).
TRIVY_UNSCANNED_RECORD = "homelab:security_trivy_unscanned_digests:count"


def trivy_running():
    """The recorded running containers (TRIVY_RUNNING_RECORD)."""
    return security_selector(TRIVY_RUNNING_RECORD)


def trivy_running_digests():
    """The digests of the images that run on this site now, one series for each
    (k8s_cluster, digest)."""
    return f"count by (k8s_cluster, digest) ({trivy_running()})"


def trivy_report_digests(expr):
    """A metric of the reports of Trivy Operator, with its image digest in the label
    `digest`, its reference in `image_ref` and its container in `container`, to
    join it with trivy_running_containers()."""
    expr = f'label_replace({expr}, "digest", "$1", "image_digest", "(.+)")'
    expr = f'label_replace({expr}, "container", "$1", "container_name", "(.+)")'
    expr = f'label_join({expr}, "image_ref", "/", "image_registry", "image_repository")'
    return f'label_join({expr}, "image_ref", ":", "image_ref", "image_tag")'


# A report belongs to a running container when it has the digest of a running
# image, or else the namespace, the container and the reference of one. The
# digest of the pod (image_id) is the one that the node pulled; the digest of
# the report is the one that the registry gave for the reference when Trivy
# scanned it. They differ when a tag moved after the node pulled it, or when
# one is the digest of an image index and the other of the image of a platform.
TRIVY_CONTAINER_MATCH = "k8s_cluster, namespace, container, image_ref"
# The running containers whose report matches by reference only: the report is
# of the image that the tag points to now, not of the image that runs.
TRIVY_BY_REFERENCE_RECORD = "homelab:security_trivy_containers_by_reference:count"
# SecurityImagesScannedByTag fires when containers have matched by reference only
# for this long: a restart of the workload in a week is the usual cycle.
TRIVY_BY_REFERENCE_HOLD = "7d"
# The check of the accepted list that mutes SecurityImagesScannedByTag of a cluster.
TRIVY_BY_REFERENCE_CHECK = "images-scanned-by-tag"
# A test that spans a week runs in its own file (week_fixtures()), with one evaluation
# each 30 minutes: at one each minute it would take minutes of the CI job.
WEEK_EVALUATION = "30m"
TRIVY_BY_REFERENCE_WEEK_TEST = ("containers matched by reference for 7 days alert; an accepted entry mutes the alert"
                                " of its cluster, and a cluster with no Trivy Operator does not alert")


def trivy_reports_window():
    """The reports of the last TRIVY_REPORT_GAP, a report gone for its new scan included."""
    return trivy_report_digests(
        f'max_over_time({security_selector("trivy_image_vulnerabilities")}[{TRIVY_REPORT_GAP}])')


def trivy_reports_of_running():
    """The series of the reports (trivy_report_digests()) of the images that run
    on this site now: by digest, or, for a namespace, container and reference with
    no report of a running digest, by that reference. So one container counts
    one image: the one that runs when a report has its digest, and else the one
    of the reports of its reference with the highest value. Two reports of one
    reference can both be there: the tag moved twice, and an old ReplicaSet keeps
    its report for up to its lifetime. The highest value is per severity, and the
    sum reads one severity (critical), so it is the value of one report: the
    worst, which never hides a fail. The metric has no time of the scan, so the
    newest report can not be chosen."""
    reports = security_selector(TRIVY_REPORTS_RECORD)
    containers = f"group by ({TRIVY_CONTAINER_MATCH}) ({trivy_running()})"
    by_digest = f"({reports} and on (k8s_cluster, digest) {trivy_running_digests()})"
    by_reference = (f"topk by ({TRIVY_CONTAINER_MATCH}, severity) (1,"
                    f" ({reports} and on ({TRIVY_CONTAINER_MATCH}) {containers}))")
    return f"{by_digest} or ({by_reference} unless on ({TRIVY_CONTAINER_MATCH}) {by_digest})"


def trivy_accepted(check=None):
    """The accepted findings of `trivy` that have not ended, with the last value of
    the last TRIVY_ACCEPTED_HOLD, so an entry stays while its series is gone."""
    labels = {"source": '="trivy"'} | ({"check": f'="{check}"'} if check else {})
    series = "homelab_security_accepted_until_timestamp_seconds{" + ",".join(
        f"{key}{value}" for key, value in labels.items()) + "}"
    return f"last_over_time({series}[{TRIVY_ACCEPTED_HOLD}]) > time()"


def trivy_unscanned_images():
    """The digests of the images that run on this site, one series for each, that
    have no report of Trivy Operator in the last TRIVY_REPORT_GAP: no report with
    their digest, and none for the same namespace, container and reference
    (TRIVY_CONTAINER_MATCH)."""
    reported = security_selector(TRIVY_REPORTS_RECORD)
    return (f"count by (k8s_cluster, digest) (({trivy_running()})"
            f" unless on (k8s_cluster, digest) ({reported})"
            f" unless on ({TRIVY_CONTAINER_MATCH}) ({reported}))")


def trivy_by_reference_containers():
    """The number of running containers of each cluster with Trivy Operator whose
    report matches by reference only (no report has their digest), and 0 when none."""
    reported = security_selector(TRIVY_REPORTS_RECORD)
    running = trivy_running()
    matched = (f"(({running}) unless on (k8s_cluster, digest) ({reported}))"
               f" and on ({TRIVY_CONTAINER_MATCH}) ({reported})")
    operator = ("group by (k8s_cluster) ("
                + security_selector("homelab_trivy_compliance_report_update_timestamp_seconds") + ")")
    return (f"(count by (k8s_cluster) ({matched}) or on (k8s_cluster) 0 * count by (k8s_cluster) ({running}))"
            f" and on (k8s_cluster) {operator}")


def security_records():
    """The two series of the contract for the source `trivy`, from the metrics of
    Trivy Operator and of kube-state-metrics on this site.

    A control of the compliance report: trivy_compliance_info has the number of
    failed resources for a control that fails, and 1 for a control that passes.
    The result is that number, or 0. The image scans: the sum, over the distinct
    images that run on this site now, of the critical vulnerabilities that have
    a fix (the scan reports only those). A site with scan reports and none of
    them gives 0; with no running image known (kube-state-metrics down), no
    result. A running image with no report for the last hour (its scan failed,
    or has not run yet) keeps a fail, and makes a pass "no evidence". A report
    belongs to a running image by its digest, or else by the namespace, the
    container and the reference of the image (TRIVY_CONTAINER_MATCH): the digest
    of the report and the digest that the node pulled (`image_id` of
    kube-state-metrics) are not always the same.

    An accepted finding of `trivy` (the reader of the encrypted list) gives 0
    until its end. That is the list of exceptions of the scanner: no second list.
    The severity of the control, in lower case, is a label of the source."""
    accepted = trivy_accepted()
    selector = security_selector

    def lower_severity(expr):
        for value in TRIVY_SEVERITIES:
            expr = f'label_replace({expr}, "severity", "{value}", "severity", "{value.upper()}")'
        return expr

    def as_system(expr):
        return f'label_replace({expr}, "system", "$1", "k8s_cluster", "(.+)")'

    def except_accepted(expr):
        return f'({expr} unless on (check, system) ({accepted})) or 0 * {expr}'

    # PromQL reads a backslash in a string as an escape, so the regex has two.
    no_input = TRIVY_CONTROLS_WITH_NO_INPUT.replace("\\", "\\\\")

    def info(status):
        return selector("trivy_compliance_info", title=f'="{TRIVY_COMPLIANCE_TITLE}"',
                        compliance_id=f'!~"{no_input}"', status=f'="{status}"')

    by_control = f'max by (k8s_cluster, compliance_id, severity) ({info("Fail")} or 0 * {info("Pass")})'
    controls = ("max by (k8s_cluster, system, check, severity) ("
                + lower_severity(as_system(
                    f'label_replace({by_control}, "check", "{TRIVY_COMPLIANCE_SPEC}/$1", "compliance_id", "(.+)")'))
                + ")")
    # Over the same window as the coverage: a report that is gone during its new
    # scan still counts, so the sum does not fall for those minutes.
    critical = selector(TRIVY_REPORTS_OF_RUNNING_RECORD, severity='="Critical"')
    any_report = selector(TRIVY_REPORTS_RECORD)
    # Only the reports of the images that run now: after a rollout to another
    # image, the report of the old image (it can stay for a day) leaves the sum
    # when its pods stop.
    running = trivy_running_digests()
    images = (f"sum by (k8s_cluster) (max by (k8s_cluster, digest) ({critical}))"
              f" or on (k8s_cluster) 0 * count by (k8s_cluster) ({any_report})"
              f" and on (k8s_cluster) count by (k8s_cluster) ({running})")
    # A fail stays a fail with an image that has no report. A pass with such an
    # image is no evidence: no result. SecurityImagesNotScanned tells the owner.
    images = f"({images}) > 0 or (({images}) unless on (k8s_cluster) ({selector(TRIVY_UNSCANNED_RECORD)}))"
    images = as_system(
        f'label_replace(label_replace({images}, "check", "image-vulnerabilities", "", ""), "severity", "critical", "", "")')
    report = selector("homelab_trivy_compliance_report_update_timestamp_seconds", name=f'="{TRIVY_COMPLIANCE_SPEC}"')
    return [
        # First: the rules below read them in the same evaluation.
        {"record": TRIVY_RUNNING_RECORD, "expr": trivy_running_containers()},
        {"record": TRIVY_REPORTS_RECORD, "expr": trivy_reports_window()},
        {"record": TRIVY_REPORTS_OF_RUNNING_RECORD, "expr": trivy_reports_of_running()},
        {"record": TRIVY_UNSCANNED_RECORD, "expr": trivy_unscanned_images()},
        {"record": "homelab_security_check_result", "expr": except_accepted(f"({controls} or {images})"),
         "labels": {"source": "trivy"}},
        {"record": "homelab_security_last_result_timestamp_seconds",
         "expr": f"max by (k8s_cluster, system) ({as_system(report)})", "labels": {"source": "trivy"}},
        # Shown on the security board, next to the images with no report: no alert.
        {"record": TRIVY_BY_REFERENCE_RECORD, "expr": trivy_by_reference_containers()},
    ]


def security_finding_records():
    """The input of SecurityFindingNew: the result of each check of high or critical
    severity on this site, one series for each check (SECURITY_FINDING_LABELS). The
    alert reads a day of this record, so it reads each sample once."""
    series = security_selector("homelab_security_check_result", severity=f'=~"{SECURITY_FINDING_SEVERITY}"')
    return [{"record": SECURITY_FINDING_RECORD, "expr": f"max by ({SECURITY_FINDING_LABELS}) ({series})"}]


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
    # The record of security_finding_records(): one value for each check.
    result = SECURITY_FINDING_RECORD
    day_before = f"max_over_time({result}[1d] offset {SECURITY_FINDING_HOLD})"
    hold_before = f"{result} offset {SECURITY_FINDING_HOLD}"
    # A standby series (SECURITY_STANDBY_JOB) is held while homelab-wsl-02 scrapes
    # its host: the federated `up` of that host is 1. The key is k8s_cluster, which
    # both sites give each host target. A loss of homelab-wsl-02 makes the federated
    # `up` stale, and a failed scrape there makes it 0: the standby then alerts.
    # The record keeps each value while it is held, so a finding that failed
    # before is not new when the hold ends.
    held = (f'label_replace(up{{job="{SECURITY_STANDBY_JOB}",source_site!=""}} == 1,'
            ' "standby", "true", "", "")')
    # A result above 0 that is above each value of the day before, or was 0 one
    # hold before, or has no value in the day before (a new check, or one back
    # after more than a day). Each term holds until the hold has passed.
    return [
        {"alert": "SecurityFindingNew",
         "expr": (f"max by (k8s_cluster, source, system) (({result} > 0 and ("
                  f"{result} > {day_before}"
                  f" or {hold_before} == 0"
                  f" or {result} unless {day_before}))"
                  f" unless on (k8s_cluster, standby) {held})"),
         "for": "10m", "labels": {"severity": "warning"},
         "annotations": {"summary": "A new finding of high severity on {{ $labels.system }}, from {{ $labels.source }}",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityFindingNew"),
                         "description": ("A check of high or critical severity fails: with more failed resources than at each time of the day before,"
                                         " after an hour with none, or with no result in the day before."
                                         " The board shows the failing safeguards of the system, and the reports of the source have the detail."
                                         " Fix the cause, or accept the finding in the encrypted list with a reason and an end date"
                                         f" (security/README.md, Accepted findings). The alert ends after {SECURITY_FINDING_HOLD[:-1]} hour.")}},
        {"alert": "SecurityImagesNotScanned",
         # Only where Trivy Operator runs: its compliance report exists there.
         "expr": (f"count by (k8s_cluster) ({security_selector(TRIVY_UNSCANNED_RECORD)}) > 0 and on (k8s_cluster)"
                  " group by (k8s_cluster) ("
                  + security_selector("homelab_trivy_compliance_report_update_timestamp_seconds") + ")"
                  # An entry of the accepted list for the cluster mutes it until its end.
                  f' unless on (k8s_cluster) label_replace({trivy_accepted(TRIVY_UNSCANNED_CHECK)},'
                  ' "k8s_cluster", "$1", "system", "(.+)")'),
         "for": TRIVY_UNSCANNED_HOLD, "labels": {"severity": "warning"},
         "annotations": {"summary": "{{ $value }} running images on {{ $labels.k8s_cluster }} have no scan report of Trivy Operator",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityImagesNotScanned"),
                         "description": ("Images that run on this cluster have had no vulnerability report for"
                                         f" {TRIVY_UNSCANNED_HOLD[:-1]} hours: a private image, a registry that the network policy"
                                         " of the scan jobs does not name, or a scan that fails. While one image has no report,"
                                         " the check image-vulnerabilities of the cluster shows a fail, or no evidence in place of a pass."
                                         " kubernetes/apps/security/trivy-operator/README.md (When a scan fails) has the steps."
                                         f" For an image that no scan can read, an entry of the accepted list with the check {TRIVY_UNSCANNED_CHECK}"
                                         " mutes the alert of the cluster until its end.")}},
        {"alert": "SecurityImagesScannedByTag",
         # The record exists only where Trivy Operator runs.
         "expr": (f"max by (k8s_cluster) ({security_selector(TRIVY_BY_REFERENCE_RECORD)}) > 0"
                  # An entry of the accepted list for the cluster mutes it until its end.
                  f' unless on (k8s_cluster) label_replace({trivy_accepted(TRIVY_BY_REFERENCE_CHECK)},'
                  ' "k8s_cluster", "$1", "system", "(.+)")'),
         "for": TRIVY_BY_REFERENCE_HOLD, "labels": {"severity": "warning"},
         "annotations": {"summary": ("{{ $value }} running containers on {{ $labels.k8s_cluster }} have a Trivy report"
                                     " of their tag, not of the image that runs"),
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityImagesScannedByTag"),
                         "description": ("For a week, containers on this cluster have run an image that no report has:"
                                         " the tag was pushed again after the node pulled it, and the report is of the image"
                                         " that the tag points to now. The check image-vulnerabilities counts that report."
                                         " Restart the workload, so that it pulls the image of the tag, or pin a digest in its"
                                         " pod spec. The board (Sources, Images scanned by tag only) has the count, and"
                                         " kubernetes/apps/security/trivy-operator/README.md (The digest of a report) the detail."
                                         f" An entry of the accepted list with the check {TRIVY_BY_REFERENCE_CHECK}"
                                         " mutes the alert of the cluster until its end.")}},
        {"alert": "SecurityCertificateExpiring",
         "expr": ('min by (k8s_cluster, namespace, lease) (kube_lease_renew_time'
                  f'{{namespace="security",lease=~"{SECURITY_CERTIFICATE_LEASES}"}}) - time()'
                  f' < {SECURITY_CERTIFICATE_DAYS} * 86400'),
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": (f"A certificate for IAM Roles Anywhere on {{{{ $labels.k8s_cluster }}}} ends within"
                                     f" {SECURITY_CERTIFICATE_DAYS} days: {{{{ $labels.lease }}}}"),
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityCertificateExpiring"),
                         "description": ("The AWS sessions of one tool of the security platform come from this certificate, and stop when it ends."
                                         " Make a new certificate and put it in its SOPS Secret:"
                                         " kubernetes/apps/security/aws-credentials/README.md has the steps."
                                         " The alert ends at the next run of the refresher.")}},
        {"alert": "SecurityTokenExpiring",
         "expr": ('min by (k8s_cluster, namespace, lease) (kube_lease_renew_time'
                  f'{{namespace="security",lease=~"{SECURITY_TOKEN_LEASES}"}}) - time()'
                  f' < {SECURITY_CERTIFICATE_DAYS} * 86400'),
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": (f"An API token of Prowler on {{{{ $labels.k8s_cluster }}}} ends within"
                                     f" {SECURITY_CERTIFICATE_DAYS} days: {{{{ $labels.lease }}}}"),
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityTokenExpiring"),
                         "description": ("The daily Prowler run of one provider reads with this token, and fails when it ends."
                                         " Make a new token, put it in its SOPS Secret, and write its end date to its Lease:"
                                         " kubernetes/apps/security/prowler/README.md has the steps."
                                         " The alert ends when the Lease has the new date.")}},
        {"alert": "SecurityAuditLinesRefused",
         "expr": ("sum by (k8s_cluster, component_id) ("
                  f"increase({errors}[{AUDIT_SINK_WINDOW}]) > 0"
                  f" or ({errors} > 0 unless {errors} offset {AUDIT_SINK_WINDOW}))"),
         "labels": {"severity": "warning"},
         "annotations": {"summary": "Loki refused audit lines of {{ $labels.k8s_cluster }}",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster={{ $labels.k8s_cluster }}',
                         "runbook_url": security_runbook("SecurityAuditLinesRefused"),
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
                         "runbook_url": security_runbook("SecurityAcceptedListUnread"),
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
                         "runbook_url": security_runbook("SecurityAcceptedEntriesSkipped"),
                         "description": ("{{ $value }} entries of the list of accepted findings do not fit, so the security converter"
                                         " skips them: their findings show as fails on the board. The Prowler runs skip the"
                                         " entries that are not in the form, and take an entry whose source or system is not in"
                                         " the registry. The log of the converter has the number of each entry and why. security/converter/README.md, \"An entry that the"
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
                                {"name": "homelab.security", "interval": "1m",
                                 "rules": security_records() + security_finding_records() + security_alerts()}]}}


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
        *ai_fixtures(),
        *house_fixtures(),
        *host_fixtures(),
        *capacity_fixtures(),
        *security_fixtures(),
        *high_check_fixtures(),
        *audit_sink_fixtures(),
        *accepted_list_fixtures(),
        *(test for test in trivy_fixtures() if test["name"] != TRIVY_BY_REFERENCE_WEEK_TEST),
    ]}


def week_fixtures():
    """The tests that span a week, with one evaluation each WEEK_EVALUATION
    (observability/tests/test_week_rule_tests.py runs them)."""
    return {"rule_files": ["rules-for-test.yaml"], "evaluation_interval": WEEK_EVALUATION,
            "tests": [test for test in trivy_fixtures() if test["name"] == TRIVY_BY_REFERENCE_WEEK_TEST]}


def security_fixtures():
    site = "homelab-wsl-02"
    day = 86400
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityCertificateExpiring")
    token_rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityTokenExpiring")

    def lease(name, ends, namespace="security"):
        """A Lease whose renew time is `ends` seconds after the start of the test."""
        return {"series": f'kube_lease_renew_time{{k8s_cluster="{site}",namespace="{namespace}",lease="{name}"}}',
                "values": f"{ends}+0x320"}

    def expected(name, alert=rule):
        labels = {"k8s_cluster": site, "namespace": "security", "lease": name}
        annotations = {}
        for key, text in alert["annotations"].items():
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
        {"name": "a token with 29 days left alerts; one with 40 days does not", "interval": "5m",
         "input_series": [lease("prowler-cloudflare-token", 29 * day), lease("prowler-example-token", 40 * day)],
         "alert_rule_test": [
             {"alertname": "SecurityTokenExpiring", "eval_time": "30m", "exp_alerts": []},
             {"alertname": "SecurityTokenExpiring", "eval_time": "70m", "exp_alerts": [
                 expected("prowler-cloudflare-token", token_rule)]},
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


def high_check_fixtures():
    """Design Doc 0004, section 5.8: the check of the Defender signatures and the MFA
    check of Terraform Cloud are high, so a fail of each starts SecurityFindingNew. The
    same fail at medium, as before, does not."""
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityFindingNew")
    site = "homelab-wsl"
    checks = (("host-check", "defender-signatures", "DESKTOP-HKE05KI"),
              ("api-check", "tfc-mfa-required", "tnwks-ops"))

    def result(source, check, system, severity):
        return {"series": (f'homelab_security_check_result{{k8s_cluster="{site}",source="{source}",'
                           f'check="{check}",system="{system}",severity="{severity}"}}'),
                "values": "1+0x30"}

    def fires(source, system):
        labels = {"k8s_cluster": site, "source": source, "system": system}
        annotations = {}
        for key, text in rule["annotations"].items():
            for label, value in labels.items():
                text = text.replace("{{ $labels." + label + " }}", value)
            annotations[key] = text
        return {"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}

    return [
        {"name": f"a fail of {check} at high starts SecurityFindingNew, and at medium it does not",
         "interval": "1m",
         "input_series": [result(source, check, system, "high"),
                          result(source, check, f"{system}-medium", "medium")],
         "alert_rule_test": [
             {"alertname": rule["alert"], "eval_time": "5m", "exp_alerts": []},
             {"alertname": rule["alert"], "eval_time": "15m", "exp_alerts": [fires(source, system)]},
         ]}
        for source, check, system in checks
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

    def lists(read, skipped, labels=target):
        return [{"series": f"{ACCEPTED_LIST_READ}{{{labels}}}", "values": read},
                {"series": f"{ACCEPTED_SKIPPED}{{{labels}}}", "values": skipped}]

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
        # The federated copy of another site's series: that site alerts on its own.
        {"name": "a federated copy of the series does not alert", "interval": "1m",
         "input_series": lists("0+0x90", "2+0x90", labels=f'{target},source_site="homelab-wsl-01"'),
         "alert_rule_test": [at(unread, 90, []), at(skipped, 90, [])]},
    ]


def trivy_fixtures():
    site = "homelab-wsl"
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityFindingNew")
    unscanned = next(rule for rule in security_alerts() if rule["alert"] == "SecurityImagesNotScanned")
    by_tag = next(rule for rule in security_alerts() if rule["alert"] == "SecurityImagesScannedByTag")

    def control(control_id, status, value, severity="HIGH", title=TRIVY_COMPLIANCE_TITLE, extra=""):
        return {"series": (f'trivy_compliance_info{{k8s_cluster="{site}",namespace="security",title="{title}",'
                           f'description="CIS Kubernetes Benchmarks",compliance_id="{control_id}",compliance_name="A control",'
                           f'status="{status}",severity="{severity}"{extra}}}'),
                "values": value}

    def image(namespace, repository, severity, value, digest="sha256:1"):
        return {"series": (f'trivy_image_vulnerabilities{{k8s_cluster="{site}",namespace="{namespace}",name="replicaset-{repository}",'
                           f'resource_kind="ReplicaSet",resource_name="{repository}",container_name="app",image_registry="ghcr.io",'
                           f'image_repository="{repository}",image_tag="1.0",image_digest="{digest}",severity="{severity}"}}'),
                "values": value}

    def running(pod, digest, metric="kube_pod_container_info", values="1+0x90"):
        return {"series": (f'{metric}{{k8s_cluster="{site}",namespace="a",pod="{pod}",container="app",'
                           f'image="ghcr.io/x:1.0",image_id="ghcr.io/x@{digest}"}}'),
                "values": values}

    def pod(namespace, name, container, spec, digest, values="1+0x90"):
        """A running container with the image of its pod spec and the digest that the node pulled."""
        repository = spec.split("@")[0].rsplit(":", 1)[0] if ":" in spec.split("/")[-1] else spec.split("@")[0]
        return {"series": (f'kube_pod_container_info{{k8s_cluster="{site}",namespace="{namespace}",pod="{name}",'
                           f'container="{container}",image_spec="{spec}",image="{spec}",image_id="{repository}@{digest}"}}'),
                "values": values}

    def report(namespace, container, reference, digest, value, severity="Critical", values=None):
        """A report of Trivy Operator for <registry>/<repository>:<tag>, with its digest."""
        registry, rest = reference.split("/", 1)
        repository, tag = rest.rsplit(":", 1)
        return {"series": (f'trivy_image_vulnerabilities{{k8s_cluster="{site}",namespace="{namespace}",name="replicaset-w-{container}",'
                           f'resource_kind="ReplicaSet",resource_name="w",container_name="{container}",image_registry="{registry}",'
                           f'image_repository="{repository}",image_tag="{tag}",image_digest="{digest}",severity="{severity}"}}'),
                "values": values or f"{value}+0x90"}

    def unscanned_digests(*digests):
        return [{"labels": f'{{k8s_cluster="{site}",digest="{digest}"}}', "value": 1} for digest in digests]

    def accepted(check, until):
        return {"series": (f'homelab_security_accepted_until_timestamp_seconds{{k8s_cluster="homelab-wsl-02",'
                           f'source_site="homelab-wsl-02",source="trivy",check="{check}",system="{site}"}}'),
                "values": f"{until}+0x20"}

    def result(check, severity, value):
        return {"labels": (f'homelab_security_check_result{{k8s_cluster="{site}",system="{site}",source="trivy",'
                           f'check="{check}",severity="{severity}"}}'),
                "value": value}

    labels = {"k8s_cluster": site, "source": "trivy", "system": site}
    annotations = {}
    for key, text in rule["annotations"].items():
        for label, value in labels.items():
            text = text.replace("{{ $labels." + label + " }}", value)
        annotations[key] = text
    fires = [{"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}]

    def at(minute, exp_alerts):
        return {"alertname": rule["alert"], "eval_time": f"{minute}m", "exp_alerts": exp_alerts}

    # One day and one hour of a steady value, then a change at minute 1500.
    before, after = 1500, 120
    return [
        {"name": "a control of the CIS report gives its failed resources, or 0; a control with no input gives nothing",
         "interval": "1m",
         "input_series": [
             control("1.2.1", "Fail", "3+0x10", severity="MEDIUM"),
             control("5.1.1", "Pass", "1+0x10"),
             control("1.4.2", "Pass", "1+0x10", severity="CRITICAL"),
             # Read nodes or etcd: no input on these clusters.
             control("1.1.1", "Pass", "1+0x10"),
             control("2.1", "Pass", "1+0x10"),
             control("4.1.1", "Fail", "1+0x10"),
             control("4.2.10", "Pass", "1+0x10"),
             # Another report, and a copy of the other site.
             control("1.2.2", "Fail", "4+0x10", title="NSA"),
             control("1.2.3", "Fail", "5+0x10", extra=',source_site="homelab-wsl-02"'),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("k8s-cis-1.23/1.2.1", "medium", 3),
                 result("k8s-cis-1.23/1.4.2", "critical", 0),
                 result("k8s-cis-1.23/5.1.1", "high", 0)]},
         ]},
        {"name": "the image scans give the critical vulnerabilities with a fix over the distinct images that run",
         "interval": "1m",
         "input_series": [
             running("one-abc", "sha256:1"),
             running("two-abc", "sha256:2"),
             running("three-abc", "sha256:3"),
             # One image in two workloads counts one time.
             image("a", "one", "Critical", "2+0x10"),
             image("b", "one", "Critical", "2+0x10"),
             image("a", "one", "High", "7+0x10"),
             image("a", "two", "Critical", "1+0x10", digest="sha256:2"),
             image("a", "three", "Critical", "0+0x10", digest="sha256:3"),
             # The report of an image that does not run (an old ReplicaSet) does not count.
             image("a", "old", "Critical", "5+0x10", digest="sha256:4"),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 3)]},
         ]},
        {"name": "scan reports with no critical vulnerability give 0, and no scan report gives nothing",
         "interval": "1m",
         "input_series": [running("one-abc", "sha256:1"),
                          image("a", "one", "Critical", "0+0x5"), image("a", "one", "High", "4+0x5")],
         "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 0)]},
             # A report counts for an hour after its last sample.
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "70m", "exp_samples": []},
         ]},
        {"name": "a fail stays while a running image has no report, and a scan pod or a starting container is not a running image",
         "interval": "1m",
         "input_series": [
             running("one-abc", "sha256:1"),
             running("one-abc", "sha256:2", metric="kube_pod_init_container_info"),
             running("scan-vulnerabilityreport-123", "sha256:9"),
             # A container that has not started has no image_id yet.
             {"series": f'kube_pod_container_info{{k8s_cluster="{site}",namespace="a",pod="new-abc",container="app",image="ghcr.io/y:1.0",image_id=""}}',
              "values": "1+0x90"},
             image("a", "one", "Critical", "2+0x90"),
             # The report of the init image: there at the start, then its scan
             # fails at minute 10 and the report is not there again.
             image("a", "init", "Critical", "0+0x10", digest="sha256:2"),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "80m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
         ]},
        {"name": "a pass while a running image has no report for an hour is no evidence",
         "interval": "1m",
         "input_series": [
             running("one-abc", "sha256:1"),
             running("one-abc", "sha256:2", metric="kube_pod_init_container_info"),
             image("a", "one", "Critical", "0+0x90"),
             image("a", "init", "Critical", "0+0x10", digest="sha256:2"),
         ], "promql_expr_test": [
             # The report of a scan that runs again is gone for less than an hour.
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "40m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 0)]},
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "80m", "exp_samples": []},
         ]},
        {"name": "a running image with no report for 36 hours alerts on a cluster that runs Trivy Operator, and not before",
         "interval": "2m",
         "input_series": [
             running("one-abc", "sha256:1", values="1+0x1200"),
             running("one-abc", "sha256:2", values="1+0x1200"),
             image("a", "one", "Critical", "0+0x1200"),
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'name="k8s-cis-1.23"}'), "values": "1700000000+0x1200"},
             # A cluster with no Trivy Operator: its images have no report, and no alert.
             {"series": ('kube_pod_container_info{k8s_cluster="homelab-wsl-02",namespace="a",pod="one-abc",container="app",'
                         'image="ghcr.io/x:1.0",image_id="ghcr.io/x@sha256:3"}'), "values": "1+0x1200"},
         ], "alert_rule_test": [
             {"alertname": "SecurityImagesNotScanned", "eval_time": "35h", "exp_alerts": []},
             {"alertname": "SecurityImagesNotScanned", "eval_time": "37h", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": site},
                  "exp_annotations": {key: text.replace("{{ $labels.k8s_cluster }}", site).replace("{{ $value }}", "1")
                                      for key, text in unscanned["annotations"].items()}}]},
         ]},
        {"name": "a pod with the digest of an image index and a report with the digest of the platform image: the report counts",
         "interval": "1m",
         "input_series": [
             pod("db", "pg-1", "postgres", "ghcr.io/example/postgres:16", "sha256:index1"),
             report("db", "postgres", "ghcr.io/example/postgres:16", "sha256:platform1", 2),
             # The other way round: the platform digest on the pod, the index digest on the report.
             pod("web", "app-1", "app", "ghcr.io/example/app:2.0", "sha256:platform2"),
             report("web", "app", "ghcr.io/example/app:2.0", "sha256:index2", 1),
         ], "promql_expr_test": [
             {"expr": trivy_unscanned_images(), "eval_time": "5m", "exp_samples": []},
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 3)]},
         ]},
        {"name": "the reference of a report matches the image of the pod spec as the operator parses it",
         "interval": "1m",
         "input_series": [
             # Docker Hub with no registry, in the namespace library.
             pod("db", "db-1", "db", "postgres:16-alpine", "sha256:a1"),
             report("db", "db", "index.docker.io/library/postgres:16-alpine", "sha256:b1", 0),
             # Docker Hub with docker.io and no tag: the tag latest.
             pod("db", "tool-1", "tool", "docker.io/example/tool", "sha256:a2"),
             report("db", "tool", "index.docker.io/example/tool:latest", "sha256:b2", 0),
             # A tag and a digest in the pod spec: the tag.
             pod("db", "proxy-1", "proxy", "quay.io/example/proxy:1.2@sha256:a3", "sha256:a3"),
             report("db", "proxy", "quay.io/example/proxy:1.2", "sha256:b3", 0),
             # A registry with a port.
             pod("db", "cache-1", "cache", "registry.example:5000/cache:7", "sha256:a4"),
             report("db", "cache", "registry.example:5000/cache:7", "sha256:b4", 0),
         ], "promql_expr_test": [
             {"expr": trivy_unscanned_images(), "eval_time": "5m", "exp_samples": []},
         ]},
        {"name": "a report of the same reference in another namespace, of another container or of another tag does not count",
         "interval": "1m",
         "input_series": [
             pod("db", "pg-1", "postgres", "ghcr.io/example/postgres:16", "sha256:c1"),
             report("other", "postgres", "ghcr.io/example/postgres:16", "sha256:d1", 0),
             pod("db", "pg-2", "postgres", "ghcr.io/example/postgres:17", "sha256:c2"),
             report("db", "sidecar", "ghcr.io/example/postgres:17", "sha256:d2", 0),
             pod("db", "pg-3", "postgres", "ghcr.io/example/postgres:18", "sha256:c3"),
             report("db", "postgres", "ghcr.io/example/postgres:18.1", "sha256:d3", 0),
             # A pod spec with a digest only joins by its digest alone.
             pod("db", "pg-4", "postgres", "ghcr.io/example/postgres@sha256:c4", "sha256:c4"),
             report("db", "postgres", "ghcr.io/example/postgres:latest", "sha256:d4", 0),
             # A report with no running container does not count.
             report("db", "gone", "ghcr.io/example/gone:1", "sha256:d5", 9),
         ], "promql_expr_test": [
             {"expr": trivy_unscanned_images(), "eval_time": "5m",
              "exp_samples": unscanned_digests("sha256:c1", "sha256:c2", "sha256:c3", "sha256:c4")},
             # A pass with images that have no report is no evidence.
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": []},
         ]},
        {"name": "a tag moves between two scans while the pod runs the old image: the report of the running digest counts, alone",
         "interval": "1m",
         "input_series": [
             pod("web", "app-1", "app", "ghcr.io/example/app:1", "sha256:d1", values="1+0x120"),
             # The report of the old image until minute 30, then the one of the new image.
             report("web", "app", "ghcr.io/example/app:1", "sha256:d1", 2, values="2+0x30"),
             report("web", "app", "ghcr.io/example/app:1", "sha256:d2", 3, values="_x31 3+0x89"),
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'name="k8s-cis-1.23"}'), "values": "1700000000+0x120"},
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "45m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
             {"expr": TRIVY_BY_REFERENCE_RECORD, "eval_time": "45m", "exp_samples": [
                 {"labels": f'{TRIVY_BY_REFERENCE_RECORD}{{k8s_cluster="{site}"}}', "value": 0}]},
             # The report of the old image is gone for more than an hour: the new one counts, by reference.
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "100m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 3)]},
             {"expr": TRIVY_BY_REFERENCE_RECORD, "eval_time": "100m", "exp_samples": [
                 {"labels": f'{TRIVY_BY_REFERENCE_RECORD}{{k8s_cluster="{site}"}}', "value": 1}]},
         ]},
        {"name": "after a restart on a moved tag, the report of the old ReplicaSet does not count next to the new one",
         "interval": "1m",
         "input_series": [
             pod("web", "app-2", "app", "ghcr.io/example/app:1", "sha256:d2"),
             {"series": ('trivy_image_vulnerabilities{k8s_cluster="homelab-wsl",namespace="web",name="replicaset-old-app",'
                         'resource_kind="ReplicaSet",resource_name="old",container_name="app",image_registry="ghcr.io",'
                         'image_repository="example/app",image_tag="1",image_digest="sha256:d1",severity="Critical"}'),
              "values": "4+0x90"},
             report("web", "app", "ghcr.io/example/app:1", "sha256:d2", 0),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "45m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 0)]},
         ]},
        {"name": "a tag moved twice and the pod runs an older image: one report of the reference counts, the worst",
         "interval": "1m",
         "input_series": [
             pod("web", "app-1", "app", "ghcr.io/example/app:1", "sha256:d0"),
             {"series": ('trivy_image_vulnerabilities{k8s_cluster="homelab-wsl",namespace="web",name="replicaset-old-app",'
                         'resource_kind="ReplicaSet",resource_name="old",container_name="app",image_registry="ghcr.io",'
                         'image_repository="example/app",image_tag="1",image_digest="sha256:d1",severity="Critical"}'),
              "values": "4+0x90"},
             report("web", "app", "ghcr.io/example/app:1", "sha256:d2", 3),
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'name="k8s-cis-1.23"}'), "values": "1700000000+0x90"},
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "10m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 4)]},
             # One container, counted once.
             {"expr": TRIVY_BY_REFERENCE_RECORD, "eval_time": "10m", "exp_samples": [
                 {"labels": f'{TRIVY_BY_REFERENCE_RECORD}{{k8s_cluster="{site}"}}', "value": 1}]},
         ]},
        {"name": TRIVY_BY_REFERENCE_WEEK_TEST,
         # One sample at each evaluation (week_fixtures()).
         "interval": WEEK_EVALUATION,
         "input_series": [
             series
             # The same container and report on the cluster of the site and on a second cluster with
             # the operator, which has an entry of the accepted list.
             for cluster in (site, "homelab-wsl-03")
             for series in (
                 {key: value.replace(f'k8s_cluster="{site}"', f'k8s_cluster="{cluster}"') for key, value in item.items()}
                 for item in (
                     pod("web", "app-1", "app", "ghcr.io/example/app:1", "sha256:d0", values="1+0x400"),
                     report("web", "app", "ghcr.io/example/app:1", "sha256:d2", 0, values="0+0x400"),
                     {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                                 'name="k8s-cis-1.23"}'), "values": "1700000000+0x400"}))
         ] + [
             # Until a time after the end of the test.
             {"series": ('homelab_security_accepted_until_timestamp_seconds{k8s_cluster="homelab-wsl-02",'
                         f'source_site="homelab-wsl-02",source="trivy",check="{TRIVY_BY_REFERENCE_CHECK}",'
                         'system="homelab-wsl-03"}'), "values": "4102444800+0x400"},
             # A cluster with no Trivy Operator: no record, no alert.
             {"series": ('kube_pod_container_info{k8s_cluster="homelab-wsl-02",namespace="web",pod="app-1",container="app",'
                         'image_spec="ghcr.io/example/app:1",image="ghcr.io/example/app:1",image_id="ghcr.io/example/app@sha256:d0"}'),
              "values": "1+0x400"},
         ], "alert_rule_test": [
             {"alertname": "SecurityImagesScannedByTag", "eval_time": "6d", "exp_alerts": []},
             {"alertname": "SecurityImagesScannedByTag", "eval_time": "7d2h", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": site},
                  "exp_annotations": {key: text.replace("{{ $labels.k8s_cluster }}", site).replace("{{ $value }}", "1")
                                      for key, text in by_tag["annotations"].items()}}]},
         ], "promql_expr_test": [
             {"expr": TRIVY_BY_REFERENCE_RECORD, "eval_time": "7d2h", "exp_samples": [
                 {"labels": f'{TRIVY_BY_REFERENCE_RECORD}{{k8s_cluster="{cluster}"}}', "value": 1}
                 for cluster in (site, "homelab-wsl-03")]},
         ]},
        {"name": "a cluster with no Trivy Operator has no count of containers matched by reference",
         "interval": "1m",
         "input_series": [pod("web", "app-1", "app", "ghcr.io/example/app:1", "sha256:d1")],
         "promql_expr_test": [{"expr": TRIVY_BY_REFERENCE_RECORD, "eval_time": "5m", "exp_samples": []}]},
        {"name": "an image with a report under another digest does not alert; an image with no report still alerts after 36 hours",
         "interval": "2m",
         "input_series": [
             pod("db", "pg-1", "postgres", "ghcr.io/example/postgres:16", "sha256:index1", values="1+0x1200"),
             report("db", "postgres", "ghcr.io/example/postgres:16", "sha256:platform1", 0, values="0+0x1200"),
             pod("db", "db-1", "db", "postgres:16-alpine", "sha256:e1", values="1+0x1200"),
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'name="k8s-cis-1.23"}'), "values": "1700000000+0x1200"},
         ], "alert_rule_test": [
             {"alertname": "SecurityImagesNotScanned", "eval_time": "37h", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": site},
                  "exp_annotations": {key: text.replace("{{ $labels.k8s_cluster }}", site).replace("{{ $value }}", "1")
                                      for key, text in unscanned["annotations"].items()}}]},
         ]},
        {"name": "an accepted entry for the images with no report mutes the alert of its cluster until its end",
         "interval": "2m",
         "input_series": [
             running("one-abc", "sha256:2", values="1+0x2340"),
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'name="k8s-cis-1.23"}'), "values": "1700000000+0x2340"},
             # Served on homelab-wsl-02 and gone for 4 hours; it ends at 40 hours.
             {"series": ('homelab_security_accepted_until_timestamp_seconds{k8s_cluster="homelab-wsl-02",'
                         f'source_site="homelab-wsl-02",source="trivy",check="{TRIVY_UNSCANNED_CHECK}",system="{site}"}}'),
              "values": f"{40 * 3600}+0x500 stale _x119 {40 * 3600}+0x1720"},
         ], "alert_rule_test": [
             {"alertname": "SecurityImagesNotScanned", "eval_time": "37h", "exp_alerts": []},
             # The 36 hours start at its end.
             {"alertname": "SecurityImagesNotScanned", "eval_time": "75h", "exp_alerts": []},
             {"alertname": "SecurityImagesNotScanned", "eval_time": "77h", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": site},
                  "exp_annotations": {key: text.replace("{{ $labels.k8s_cluster }}", site).replace("{{ $value }}", "1")
                                      for key, text in unscanned["annotations"].items()}}]},
         ]},
        {"name": "scan reports with no running image known (kube-state-metrics down) give nothing",
         "interval": "1m",
         "input_series": [image("a", "one", "Critical", "2+0x10"), image("a", "two", "Critical", "0+0x10", digest="sha256:2")],
         "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": []},
         ]},
        # A rollout at minute 1500: the old pods stop at minute 1503, the scan
        # of the new image ends at minute 1505, and the old report stays.
        {"name": "a rollout to an image with the same critical count does not alert, and the old report does not count",
         "interval": "1m",
         "input_series": [
             running("one-old", "sha256:1", values=f"1+0x{before + 2} stale"),
             running("one-new", "sha256:5", values=f"_x{before} 1+0x{after}"),
             image("a", "one", "Critical", f"2+0x{before + after}"),
             image("a", "one", "Critical", f"_x{before + 5} 2+0x{after - 5}", digest="sha256:5"),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 1}m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
             # The new image has no report yet: no evidence.
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 4}m", "exp_samples": []},
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 10}m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 100}m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
         ], "alert_rule_test": [at(before + 10, []), at(before + 20, []), at(before + 70, [])]},
        {"name": "a rollout to an image with more critical vulnerabilities alerts",
         "interval": "1m",
         "input_series": [
             running("one-old", "sha256:1", values=f"1+0x{before + 2} stale"),
             running("one-new", "sha256:5", values=f"_x{before} 1+0x{after}"),
             image("a", "one", "Critical", f"2+0x{before + after}"),
             image("a", "one", "Critical", f"_x{before + 5} 3+0x{after - 5}", digest="sha256:5"),
         ], "alert_rule_test": [at(before + 10, []), at(before + 20, fires)]},
        {"name": "a report gone for 15 minutes during its new scan changes no result and does not alert",
         "interval": "1m",
         "input_series": [
             running("one-abc", "sha256:1", values=f"1+0x{before + after}"),
             image("a", "one", "Critical", f"2+0x{before} stale _x14 2+0x{after - 15}"),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 10}m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 2)]},
         ], "alert_rule_test": [at(before + 15, []), at(before + 70, []), at(before + 80, [])]},
        # time() is 300 at minute 5. The accepted list is served on
        # homelab-wsl-02, and homelab-wsl reads it by its federation.
        {"name": "an accepted check gives 0 until its end, then its result again",
         "interval": "1m",
         "input_series": [
             control("1.2.1", "Fail", "3+0x10"),
             control("1.2.8", "Fail", "1+0x10"),
             running("one-abc", "sha256:1"),
             image("a", "one", "Critical", "2+0x10"),
             accepted("k8s-cis-1.23/1.2.1", 100000),
             accepted("image-vulnerabilities", 100000),
             accepted("k8s-cis-1.23/1.2.8", 200),
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 result("image-vulnerabilities", "critical", 0),
                 result("k8s-cis-1.23/1.2.1", "high", 0),
                 result("k8s-cis-1.23/1.2.8", "high", 1)]},
         ]},
        {"name": "the time of the last result is the time of the compliance report of this site",
         "interval": "1m",
         "input_series": [
             {"series": (f'homelab_trivy_compliance_report_update_timestamp_seconds{{k8s_cluster="{site}",'
                         'customresource_group="aquasecurity.github.io",customresource_kind="ClusterComplianceReport",'
                         'customresource_version="v1alpha1",name="k8s-cis-1.23"}'), "values": "1700000000+0x10"},
             {"series": ('homelab_trivy_compliance_report_update_timestamp_seconds{k8s_cluster="homelab-wsl-02",'
                         'source_site="homelab-wsl-02",name="k8s-cis-1.23"}'), "values": "1800000000+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab_security_last_result_timestamp_seconds{source="trivy"}', "eval_time": "5m", "exp_samples": [
                 {"labels": (f'homelab_security_last_result_timestamp_seconds{{k8s_cluster="{site}",system="{site}",'
                             'source="trivy"}'), "value": 1700000000}]},
         ]},
        {"name": "a result of high severity above each value of the day before alerts for one hour",
         "interval": "1m",
         "input_series": [control("5.1.2", "Fail", f"1+0x{before} 2+0x{after}")],
         "alert_rule_test": [at(before - 10, []), at(before + 5, []), at(before + 15, fires),
                             at(before + 55, fires), at(before + 70, [])]},
        {"name": "a result that fails again after an hour at 0 alerts, also below a larger fail of the day before",
         "interval": "1m",
         "input_series": [control("5.1.2", "Fail", f"3+0x600 0+0x600 1+0x{after}")],
         "alert_rule_test": [at(1205, []), at(1215, fires), at(1255, fires), at(1270, [])]},
        {"name": "a check with no result in the day before alerts when its first result fails", "interval": "1m",
         "input_series": [
             {"series": ('homelab_security_check_result{k8s_cluster="homelab-wsl-02",source="prowler",system="aws-prod",'
                         'check="iam_root_mfa_enabled",severity="critical"}'),
              "values": f"_x{before} 1+0x{after}"},
             # A new check that passes does not alert.
             {"series": ('homelab_security_check_result{k8s_cluster="homelab-wsl-02",source="prowler",system="aws-prod",'
                         'check="iam_root_hardware_mfa_enabled",severity="critical"}'),
              "values": f"_x{before} 0+0x{after}"},
         ],
         "alert_rule_test": [
             {"alertname": rule["alert"], "eval_time": f"{before + 15}m", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": "homelab-wsl-02", "source": "prowler",
                                 "system": "aws-prod"},
                  "exp_annotations": {key: text.replace("{{ $labels.system }}", "aws-prod")
                                      .replace("{{ $labels.source }}", "prowler")
                                      .replace("{{ $labels.k8s_cluster }}", "homelab-wsl-02")
                                      for key, text in rule["annotations"].items()}}]},
             {"alertname": rule["alert"], "eval_time": f"{before + 75}m", "exp_alerts": []}]},
        {"name": "a control whose severity changes from high to critical does not alert", "interval": "1m",
         "input_series": [control("5.1.2", "Fail", f"1+0x{before} _x{after}"),
                          control("5.1.2", "Fail", f"_x{before} 1+0x{after}", severity="CRITICAL")],
         "alert_rule_test": [at(before + 15, []), at(before + 60, [])]},
        {"name": "an accepted check of homelab-wsl stays muted while the federated list is gone for hours", "interval": "1m",
         "input_series": [
             control("1.2.1", "Fail", f"1+0x{before + 400}"),
             {"series": ('homelab_security_accepted_until_timestamp_seconds{k8s_cluster="homelab-wsl-02",'
                         f'source_site="homelab-wsl-02",source="trivy",check="k8s-cis-1.23/1.2.1",system="{site}"}}'),
              "values": f"1000000000+0x{before} stale _x239 1000000000+0x160"},
         ], "promql_expr_test": [
             {"expr": 'homelab_security_check_result{source="trivy"}', "eval_time": f"{before + 120}m", "exp_samples": [
                 result("k8s-cis-1.23/1.2.1", "high", 0)]},
         ], "alert_rule_test": [at(before + 20, []), at(before + 120, []), at(before + 250, [])]},
        {"name": "a result whose pod label changes during the day does not alert", "interval": "1m",
         "input_series": [
             {"series": ('homelab_security_check_result{k8s_cluster="homelab-wsl-02",source="prowler",system="aws-prod",'
                         'check="iam_root_mfa_enabled",severity="critical",pod="converter-a",instance="10.0.0.1:9090"}'),
              "values": f"2+0x{before} _x{after}"},
             {"series": ('homelab_security_check_result{k8s_cluster="homelab-wsl-02",source="prowler",system="aws-prod",'
                         'check="iam_root_mfa_enabled",severity="critical",pod="converter-b",instance="10.0.0.2:9090"}'),
              "values": f"_x{before} 2+0x{after}"},
         ],
         "alert_rule_test": [at(before + 15, []), at(before + 60, [])]},
        {"name": "a control of high severity that changes from pass to fail alerts", "interval": "1m",
         "input_series": [control("5.1.2", "Pass", f"1+0x{before}"),
                          control("5.1.2", "Fail", f"_x{before} 1+0x{after}")],
         "alert_rule_test": [at(before - 10, []), at(before + 15, fires)]},
        {"name": "a result of low severity, a steady result, a result that falls and a copy of the other site do not alert",
         "interval": "1m",
         "input_series": [
             control("1.2.19", "Fail", f"1+0x{before} 2+0x{after}", severity="LOW"),
             control("5.1.1", "Fail", f"2+0x{before + after}"),
             control("5.1.3", "Fail", f"3+0x{before} 1+0x{after}"),
             control("5.1.5", "Fail", f"1+0x{before} 2+0x{after}", extra=',source_site="homelab-wsl-02"'),
         ],
         "alert_rule_test": [at(before + 15, []), at(before + 60, [])]},
        {"name": "a result that comes back after a gap of less than a day does not alert", "interval": "1m",
         "input_series": [control("5.1.2", "Fail", f"2+0x600 _x600 2+0x{after}")],
         "alert_rule_test": [at(1215, []), at(1260, [])]},
        *standby_fixtures(),
    ]


def standby_fixtures():
    """Design Doc 0004, section 5.5: homelab-wsl holds the series of the host check
    (label standby) as the standby of homelab-wsl-02. A new finding of a standby
    series alerts only while homelab-wsl-02 does not scrape its host: no federated
    `up` of that host (site 2 is away), or an `up` of 0 (site 2 can not reach it)."""
    rule = next(rule for rule in security_alerts() if rule["alert"] == "SecurityFindingNew")
    host, other = "homelab-wsl", "homelab-wsl-02"

    def result(values, system="DESKTOP-HKE05KI", cluster=host, standby=True):
        mark = ',standby="true"' if standby else ""
        return {"series": (f'homelab_security_check_result{{k8s_cluster="{cluster}",job="windows-exporter",'
                           f'instance="{system}",source="host-check",system="{system}",check="defender-enabled",'
                           f'severity="high"{mark}}}'),
                "values": values}

    def up(values, cluster=host, instance="DESKTOP-HKE05KI"):
        return {"series": (f'up{{job="{SECURITY_STANDBY_JOB}",instance="{instance}",k8s_cluster="{cluster}",'
                           f'source_site="{other}"}}'),
                "values": values}

    def fires(system="DESKTOP-HKE05KI", cluster=host):
        labels = {"k8s_cluster": cluster, "source": "host-check", "system": system}
        annotations = {}
        for key, text in rule["annotations"].items():
            for label, value in labels.items():
                text = text.replace("{{ $labels." + label + " }}", value)
            annotations[key] = text
        return [{"exp_labels": {"severity": "warning", **labels}, "exp_annotations": annotations}]

    def at(minute, exp_alerts):
        return {"alertname": rule["alert"], "eval_time": f"{minute}m", "exp_alerts": exp_alerts}

    # A day and an hour of a pass, then a fail at minute 1500.
    before, after = 1500, 120
    new_fail = f"0+0x{before} 1+0x{after}"
    return [
        {"name": "a new finding of a standby series is held while site 2 scrapes its host", "interval": "1m",
         "input_series": [result(new_fail), up(f"1+0x{before + after}")],
         "alert_rule_test": [at(before + 15, []), at(before + 60, [])]},
        {"name": "a new finding of a standby series alerts while site 2 is away", "interval": "1m",
         "input_series": [result(new_fail), up(f"1+0x{before - 100}")],
         "alert_rule_test": [at(before - 10, []), at(before + 15, fires())]},
        # Reviewer's note: the hold is the scrape of that host on site 2, not
        # the answer of the Prometheus of site 2.
        {"name": "a new finding of a standby series alerts while site 2 answers but can not scrape its host",
         "interval": "1m",
         "input_series": [result(new_fail), up(f"1+0x{before} 0+0x{after}"),
                          up(f"1+0x{before + after}", cluster=other, instance="WSL-02")],
         "alert_rule_test": [at(before + 15, fires())]},
        {"name": "the hold of one host does not hold the standby series of the other host", "interval": "1m",
         "input_series": [result(new_fail, system="WSL-02", cluster=other), up(f"1+0x{before + after}")],
         "alert_rule_test": [at(before + 15, fires(system="WSL-02", cluster=other))]},
        {"name": "a finding that failed while held is not new when the hold ends", "interval": "1m",
         "input_series": [result(f"0+0x60 1+0x{before + after - 60}"), up(f"1+0x{before}")],
         "alert_rule_test": [at(before + 15, []), at(before + 60, [])]},
        {"name": "a series with no standby label is not held by the scrape of site 2", "interval": "1m",
         "input_series": [result(new_fail, standby=False), up(f"1+0x{before + after}")],
         "alert_rule_test": [at(before + 15, fires())]},
    ]


def capacity_fixtures():
    site = "homelab-wsl-02"
    gib = 1024 ** 3

    def expected(name, **labels):
        """The alert as promtool sees it: its annotations with the labels filled in."""
        severity = labels.pop("severity", "warning")
        rule = next(rule for rule in capacity_alerts()
                    if rule["alert"] == name and rule["labels"]["severity"] == severity)
        labels = {"k8s_cluster": site, **labels}
        annotations = {}
        for key, text in rule["annotations"].items():
            for label, value in labels.items():
                text = text.replace("{{ $labels." + label + " }}", value)
            annotations[key] = text
        return {"exp_labels": {"severity": rule["labels"]["severity"], **labels}, "exp_annotations": annotations}

    def node(name, value, extra=""):
        return {"series": f'node_memory_working_set_bytes{{k8s_cluster="{site}",node="{name}"{extra}}}',
                "values": f"{value}+0x30"}

    def site1_node(name, value):
        return {"series": f'node_memory_working_set_bytes{{k8s_cluster="{SITE1_CLUSTER}",node="{name}"}}',
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
        # Made-up node names of the form of Talos in Docker.
        {"name": "a worker of site 1 past 90% of its limit warns after 5 minutes; the control plane and site 2 at the same figure do not",
         "interval": "1m",
         "input_series": [
             # 12 of 13 GiB: 92%.
             site1_node("lab-worker-1", int(12 * gib)),
             site1_node("lab-worker-2", int(11 * gib)),
             site1_node("lab-controlplane-1", int(12 * gib)),
             node("other-worker-1", int(12 * gib)),
         ], "alert_rule_test": [
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "4m", "exp_alerts": []},
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "20m", "exp_alerts": [
                 expected("WorkerContainerMemoryNearLimit", k8s_cluster=SITE1_CLUSTER, node="lab-worker-1")]},
         ]},
        {"name": "a worker of site 1 past 95% of its limit is critical after 2 minutes, and also warns after 5", "interval": "1m",
         "input_series": [
             # 12.5 of 13 GiB: 96%.
             site1_node("lab-worker-1", int(12.5 * gib)),
         ], "alert_rule_test": [
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "1m", "exp_alerts": []},
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "3m", "exp_alerts": [
                 expected("WorkerContainerMemoryNearLimit", k8s_cluster=SITE1_CLUSTER, node="lab-worker-1",
                          severity="critical")]},
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "6m", "exp_alerts": [
                 expected("WorkerContainerMemoryNearLimit", k8s_cluster=SITE1_CLUSTER, node="lab-worker-1"),
                 expected("WorkerContainerMemoryNearLimit", k8s_cluster=SITE1_CLUSTER, node="lab-worker-1",
                          severity="critical")]},
         ]},
        {"name": "a worker of site 1 at 89% of its limit does not alert", "interval": "1m",
         "input_series": [site1_node("lab-worker-1", int(0.89 * SITE1_WORKER_LIMIT_GIB * gib))],
         "alert_rule_test": [
             {"alertname": "WorkerContainerMemoryNearLimit", "eval_time": "20m", "exp_alerts": []},
         ]},
        {"name": "the control plane of site 1 past 98% of its limit warns after 15 minutes; a worker at the same figure does not",
         "interval": "1m",
         "input_series": [
             # 3.95 of 4 GiB: 99%.
             site1_node("lab-controlplane-1", int(3.95 * gib)),
             site1_node("lab-worker-1", int(3.95 * gib)),
             node("other-controlplane-1", int(3.95 * gib)),
         ], "alert_rule_test": [
             {"alertname": "ControlPlaneContainerMemoryNearLimit", "eval_time": "14m", "exp_alerts": []},
             {"alertname": "ControlPlaneContainerMemoryNearLimit", "eval_time": "16m", "exp_alerts": [
                 expected("ControlPlaneContainerMemoryNearLimit", k8s_cluster=SITE1_CLUSTER, node="lab-controlplane-1")]},
         ]},
        {"name": "the control plane of site 1 at 97% of its limit does not alert, nor at 92%", "interval": "1m",
         "input_series": [
             site1_node("lab-controlplane-1", int(0.97 * SITE1_CONTROLPLANE_LIMIT_GIB * gib)),
             site1_node("lab-controlplane-2", int(0.92 * SITE1_CONTROLPLANE_LIMIT_GIB * gib)),
         ],
         "alert_rule_test": [
             {"alertname": "ControlPlaneContainerMemoryNearLimit", "eval_time": "20m", "exp_alerts": []},
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


def ai_fixtures():
    """The AI states (ai_records): each state of a part, a scheduled check, a result
    line and a backup, a row whose source is gone, and a restore result kept past
    the 5 minutes of its line. Then the request panel of the AI board, which leaves
    out the GET with 405 of a client start."""
    from dashboards.boards.ai import mcp
    part = ('homelab_%s_ai_part_info{k8s_cluster="%s",observer_cluster="%s",trust_domain="main",part="%s",'
            'namespace="agents-main",kind="%s",workload="%s"}')
    out = 'homelab:ai_part_state{k8s_cluster="%s",trust_domain="main",part="%s",namespace="agents-main",kind="%s",workload="%s"}'
    kube = {"deployment": ("kube_deployment_spec_replicas", "kube_deployment_status_replicas_available"),
            "statefulset": ("kube_statefulset_replicas", "kube_statefulset_status_replicas_ready")}
    parts = (("ok", "deployment", 1, 1, 1), ("some", "statefulset", 2, 1, 2), ("down", "deployment", 1, 0, 3),
             ("zero", "deployment", 0, 0, 4))
    job = 'namespace="agents-main",cronjob="%s"'
    schedule = 'homelab_ai_schedule_objective_seconds{k8s_cluster="one",observer_cluster="one",trust_domain="main",%s}'
    result = 'homelab_agent_platform_result_timestamp_seconds{k8s_cluster="one",pod_namespace="agents-%s",app="agent",check="%s",result="%s"}'
    expected_result = 'homelab_expected_ai_result_info{k8s_cluster="one",observer_cluster="one",trust_domain="%s",check="%s"}'
    backup = 'homelab_ai_backup_objective_seconds{k8s_cluster="one",observer_cluster="one",trust_domain="main",namespace="agents-main",policy="%s"}'
    requests = 'agentgateway_requests_total{k8s_cluster="one",route="default/finance",method="%s",status="%s"}'
    key_check = 'namespace="ai",cronjob="litellm-key-check"'
    plan = ('homelab_model_bridge_plan_%s{k8s_cluster="one",namespace="monitoring",window="%s",'
            'pod="vector-agent-platform-%s"}')
    return [
        {"name": "AI parts: ready, some, none, zero replicas, gone, and absent by design", "interval": "1m",
         "input_series": [
             *[{"series": part % ("expected", "one", observer, name, kind, name), "values": "1+0x10"}
               for name, kind, *_ in parts for observer in ("one", "two")],
             *[{"series": f'{kube[kind][0]}{{k8s_cluster="one",namespace="agents-main",{kind}="{name}"}}', "values": f"{desired}+0x10"}
               for name, kind, desired, _, _ in parts],
             *[{"series": f'{kube[kind][1]}{{k8s_cluster="one",namespace="agents-main",{kind}="{name}"}}', "values": f"{ready}+0x10"}
               for name, kind, _, ready, _ in parts],
             # Declared, but kube-state-metrics has no such workload: it stays a row.
             {"series": part % ("expected", "one", "one", "gone", "deployment", "gone"), "values": "1+0x10"},
             # Site two does not run it by design.
             {"series": part % ("absent", "two", "one", "ok", "deployment", "ok"), "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:ai_part_state", "eval_time": "5m", "exp_samples": [
                 *[{"labels": out % ("one", name, kind, name), "value": state} for name, kind, _, _, state in parts],
                 {"labels": out % ("one", "gone", "deployment", "gone"), "value": 0},
                 {"labels": out % ("two", "ok", "deployment", "ok"), "value": 5},
             ]},
         ]},
        {"name": "AI scheduled checks: good, stale, failed after a good run, never good, suspended, no evidence",
         "interval": "1m",
         "input_series": [
             *[{"series": schedule % (job % name), "values": "3600+0x110"}
               for name in ("good", "stale", "failed", "never", "paused", "unseen", "recovered")],
             *[{"series": f'kube_cronjob_status_last_successful_time{{k8s_cluster="one",{job % name}}}', "values": f"{time}+0x110"}
               for name, time in (("good", 5000), ("stale", 100), ("failed", 1000), ("paused", 5000), ("recovered", 5000))],
             {"series": f'kube_cronjob_spec_suspend{{k8s_cluster="one",{job % "paused"}}}', "values": "1+0x110"},
             *[series for name in ("failed", "never", "recovered") for series in (
                 {"series": f'kube_job_owner{{k8s_cluster="one",namespace="agents-main",job_name="{name}-1",owner_kind="CronJob",owner_name="{name}"}}',
                  "values": "1+0x110"},
                 {"series": f'kube_job_status_start_time{{k8s_cluster="one",namespace="agents-main",job_name="{name}-1"}}',
                  "values": "4000+0x110"},
                 {"series": f'kube_job_status_failed{{k8s_cluster="one",namespace="agents-main",job_name="{name}-1",reason="BackoffLimitExceeded"}}',
                  "values": "1+0x110"})],
             # A failed run of a CronJob in a namespace that is not the domain's.
             {"series": 'kube_job_status_start_time{k8s_cluster="one",namespace="apps",job_name="good-1"}', "values": "5500+0x110"},
             # A declared schedule outside agents-* and monitoring (the key check of the model gateway).
             {"series": schedule.replace('trust_domain="main"', 'trust_domain="shared"') % key_check,
              "values": "86400+0x110"},
             {"series": f'kube_cronjob_status_last_successful_time{{k8s_cluster="one",{key_check}}}', "values": "5000+0x110"},
         ], "promql_expr_test": [
             {"expr": "homelab:ai_schedule_state", "eval_time": "100m", "exp_samples": [
                 *[{"labels": f'homelab:ai_schedule_state{{k8s_cluster="one",trust_domain="main",{job % name}}}', "value": value}
                   for name, value in (("good", 1), ("stale", 2), ("failed", 3), ("never", 3), ("paused", 4),
                                       ("unseen", 0), ("recovered", 1))],
                 {"labels": 'homelab:ai_schedule_state{k8s_cluster="one",trust_domain="shared",namespace="ai",cronjob="litellm-key-check"}',
                  "value": 1}]},
         ]},
        {"name": "AI result lines: the newest line in its window decides, no line is Unknown", "interval": "1m",
         "input_series": [
             *[{"series": expected_result % (domain, check), "values": "1+0x60"}
               for domain, check in (("pilot", "instruction-check"), ("main", "instruction-check"),
                                     ("finance", "instruction-check"), ("main", "memory-index"),
                                     ("finance", "automations-sync"), ("workflows", "app-drift"))],
             {"series": result % ("pilot", "instruction-check", "first"), "values": "1000+0x60"},
             {"series": result % ("main", "instruction-check", "ok"), "values": "1000+0x60"},
             {"series": result % ("main", "instruction-check", "failed"), "values": "2000+0x60"},
             {"series": result % ("finance", "instruction-check", "failed"), "values": "1000+0x60"},
             {"series": result % ("finance", "instruction-check", "ok"), "values": "2000+0x60"},
             {"series": result % ("finance", "automations-sync", "failed"), "values": "3000+0x60"},
             # A good line 55 minutes ago is outside the 30 minutes of app-drift.
             {"series": result % ("workflows", "app-drift", "ok"), "values": "100+0x5"},
         ], "promql_expr_test": [
             {"expr": "homelab:ai_result_state", "eval_time": "60m", "exp_samples": [
                 {"labels": f'homelab:ai_result_state{{k8s_cluster="one",trust_domain="{domain}",check="{check}"}}', "value": value}
                 for domain, check, value in (("pilot", "instruction-check", 1), ("main", "instruction-check", 3),
                                              ("finance", "instruction-check", 1), ("main", "memory-index", 0),
                                              ("finance", "automations-sync", 3), ("workflows", "app-drift", 0))]},
         ]},
        {"name": "AI backups: within the objective, past it, no evidence", "interval": "1m",
         "input_series": [
             *[{"series": backup % name, "values": "100+0x10"} for name in ("fresh", "old", "none")],
             {"series": 'kopiur_policy_last_backup_success_timestamp_seconds{k8s_cluster="one",namespace="agents-main",policy="fresh"}', "values": "550+0x10"},
             {"series": 'kopiur_policy_last_backup_success_timestamp_seconds{k8s_cluster="one",namespace="agents-main",policy="old"}', "values": "100+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:ai_backup_state", "eval_time": "10m", "exp_samples": [
                 {"labels": f'homelab:ai_backup_state{{k8s_cluster="one",trust_domain="main",namespace="agents-main",policy="{name}"}}', "value": value}
                 for name, value in (("fresh", 1), ("old", 3), ("none", 0))]},
             {"expr": "homelab:ai_backup_age_seconds", "eval_time": "10m", "exp_samples": [
                 {"labels": f'homelab:ai_backup_age_seconds{{k8s_cluster="one",trust_domain="main",namespace="agents-main",policy="{name}"}}', "value": value}
                 for name, value in (("fresh", 50), ("old", 500))]},
         ]},
        {"name": "AI plan use: the newest reading of any pod, until its window resets", "interval": "10m",
         "input_series": [
             # Pod a read the five hour window at 0, then Vector restarted, and
             # pod b read it at 2h (near the limit). Each gauge lives one hour.
             *[{"series": plan % (gauge, "five_hour", "a"), "values": f"{value}x6 _x30"}
               for gauge, value in (("utilization_ratio", 0.3), ("reset_timestamp_seconds", 18000),
                                    ("status", 0), ("observed_timestamp_seconds", 0))],
             *[{"series": plan % (gauge, "five_hour", "b"), "values": f"_x12 {value}x6 _x20"}
               for gauge, value in (("utilization_ratio", 0.6), ("reset_timestamp_seconds", 18000),
                                    ("status", 1), ("observed_timestamp_seconds", 7200))],
             # The week, read once at 0, resets in 10 days.
             *[{"series": plan % (gauge, "seven_day", "a"), "values": f"{value}x6 _x30"}
               for gauge, value in (("utilization_ratio", 0.1), ("reset_timestamp_seconds", 864000),
                                    ("status", 0), ("observed_timestamp_seconds", 0))],
             # A window at its limit with no share and no reset time: it counts
             # for 6 hours.
             *[{"series": plan % (gauge, "seven_day_opus", "a"), "values": f"{value}x6 _x30"}
               for gauge, value in (("utilization_ratio", -1), ("reset_timestamp_seconds", -1),
                                    ("status", 2), ("observed_timestamp_seconds", 0))],
         ], "promql_expr_test": [
             {"expr": "homelab:ai_plan_utilization_ratio", "eval_time": "4h", "exp_samples": [
                 {"labels": 'homelab:ai_plan_utilization_ratio{k8s_cluster="one",window="five_hour"}', "value": 0.6},
                 {"labels": 'homelab:ai_plan_utilization_ratio{k8s_cluster="one",window="seven_day"}', "value": 0.1}]},
             {"expr": "homelab:ai_plan_state", "eval_time": "4h", "exp_samples": [
                 {"labels": 'homelab:ai_plan_state{k8s_cluster="one",window="five_hour"}', "value": 2},
                 {"labels": 'homelab:ai_plan_state{k8s_cluster="one",window="seven_day"}', "value": 1},
                 {"labels": 'homelab:ai_plan_state{k8s_cluster="one",window="seven_day_opus"}', "value": 3}]},
             {"expr": "homelab:ai_plan_reset_timestamp_seconds", "eval_time": "4h", "exp_samples": [
                 {"labels": 'homelab:ai_plan_reset_timestamp_seconds{k8s_cluster="one",window="five_hour"}', "value": 18000},
                 {"labels": 'homelab:ai_plan_reset_timestamp_seconds{k8s_cluster="one",window="seven_day"}', "value": 864000}]},
             {"expr": "homelab:ai_plan_observed_timestamp_seconds", "eval_time": "4h", "exp_samples": [
                 {"labels": 'homelab:ai_plan_observed_timestamp_seconds{k8s_cluster="one",window="five_hour"}', "value": 7200},
                 {"labels": 'homelab:ai_plan_observed_timestamp_seconds{k8s_cluster="one",window="seven_day"}', "value": 0},
                 {"labels": 'homelab:ai_plan_observed_timestamp_seconds{k8s_cluster="one",window="seven_day_opus"}', "value": 0}]},
             # After 5h the five hour window is new; after 6h the reading with
             # no reset time no longer counts. The week stays.
             {"expr": "homelab:ai_plan_state", "eval_time": "370m", "exp_samples": [
                 {"labels": 'homelab:ai_plan_state{k8s_cluster="one",window="seven_day"}', "value": 1}]},
             {"expr": "homelab:ai_plan_utilization_ratio", "eval_time": "370m", "exp_samples": [
                 {"labels": 'homelab:ai_plan_utilization_ratio{k8s_cluster="one",window="seven_day"}', "value": 0.1}]},
         ]},
        {"name": "AI restore results keep their time after the line is gone", "interval": "1m",
         "input_series": [
             {"series": result % ("finance", "restore-check", "ok"), "values": "600+0x5"},
             {"series": result % ("finance", "vault-restore-check", "failed"), "values": "_x20 1500+0x5"},
             # Another check is not a restore test.
             {"series": result % ("finance", "daily-backup", "ok"), "values": "700+0x5"},
         ], "promql_expr_test": [
             {"expr": "homelab:ai_restore_result_timestamp_seconds", "eval_time": "3m", "exp_samples": [
                 {"labels": 'homelab:ai_restore_result_timestamp_seconds{k8s_cluster="one",trust_domain="finance",check="restore-check",result="ok"}', "value": 600}]},
             {"expr": "homelab:ai_restore_result_timestamp_seconds", "eval_time": "90m", "exp_samples": [
                 {"labels": 'homelab:ai_restore_result_timestamp_seconds{k8s_cluster="one",trust_domain="finance",check="restore-check",result="ok"}', "value": 600},
                 {"labels": 'homelab:ai_restore_result_timestamp_seconds{k8s_cluster="one",trust_domain="finance",check="vault-restore-check",result="failed"}', "value": 1500}]},
         ]},
        {"name": "the MCP request panel leaves out the GET with 405 of a client start", "interval": "1m",
         "input_series": [
             {"series": requests % ("GET", "405"), "values": "0+1x20"},
             {"series": requests % ("POST", "200"), "values": "0+2x20"},
             {"series": requests % ("POST", "405"), "values": "0+1x20"},
         ], "promql_expr_test": [
             {"expr": f'sum by (k8s_cluster, route, method, status) ({mcp("10m")})'.replace("$cluster", ".*"),
              "eval_time": "20m", "exp_samples": [
                  {"labels": '{k8s_cluster="one",route="default/finance",method="POST",status="200"}', "value": 20},
                  {"labels": '{k8s_cluster="one",route="default/finance",method="POST",status="405"}', "value": 10}]},
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
        ROOT / "observability/generated/rule-tests-week.yaml": week_fixtures(),
    }
    for path, data in result.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + yaml.safe_dump(data, sort_keys=False, width=110))


if __name__ == "__main__":
    generate()
