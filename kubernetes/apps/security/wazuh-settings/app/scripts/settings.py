"""Step 2 of the settings of the Wazuh indexer: the retention of git.

The Job wazuh-settings-git-<hash> runs this script once for each version of its
files (config/retention.json, this script and indexer.py), so a merge that
changes one of them runs it, and the CronJob wazuh-settings runs it once a day
(kubernetes/apps/security/wazuh/README.md, "Retention"). It runs as
tnwks-settings, which has no right to the Security API, and holds only the
password of that user. In this order:

  1. Waits until tnwks-settings can log in: step 1 makes it.
  2. Writes one ISM policy of tnwks for each stream of retention.json, under an
     ID of its own (never one of the shipped IDs), when it is missing or
     differs.
  3. Sets engine.index_raw_events through the Setup API
     (wazuh-indexer-plugins v5.0.0-rc2, docs/ref/modules/setup/api-reference.md).
  4. Moves each index of the patterns of a stream that is not on the current
     version of its policy (change_policy, or add for an index with no
     policy), and waits until the ISM explain API names the policy and its
     version on each one. A run that writes no policy starts a move for each
     index that is not on its policy (the first index of a stream, which the
     Setup plugin puts on the shipped policy, or one that ISM put on another
     policy), and no other. A restart of the indexer before that drops a
     move that waits (docs/ref/modules/setup/retention.md, "Opting
     in to a hard time ceiling"). So it moves nothing when the certificates
     of the indexer renew within --margin seconds: Reloader restarts the
     indexer at a renewal. That guard reads the certificate in the TLS
     handshake and the CA file of the pod, and needs no right in Kubernetes.
  5. Counts the indices of those patterns that are not on the policy of tnwks
     of their stream, with those that have no policy, those whose policy
     stopped at a failed step, and the documents of the streams that must
     hold none. It writes the counts to
     wazuh-settings/indices.json on the volume of results, where the converter
     reads them (security/converter/README.md, "The files of results").

The run exits 1 at the first error, and, after it writes the counts, when a
move did not apply in --wait seconds or an index is at a failed step of ISM,
so KubeJobFailed fires. ISM runs each step of a policy of tnwks with the roles
of tnwks-settings, the user that wrote and moved it (index-management 3.6.0.0,
ManagedIndexRunner): a step that the role does not allow fails, and only the
count shows it. Tests:
security/tests/test_wazuh_settings.py.
"""

import argparse
import fcntl
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.parse
from pathlib import Path

import monitors
from indexer import Indexer, NoAnswer, Stop, password_ok, q, read_secret, tls_context

# The policy IDs that the Setup plugin writes again at each election of a
# cluster manager (plugins/setup/src/main/resources/policies/ at v5.0.0-rc2).
# A policy of tnwks never takes one of them.
SHIPPED_POLICY_IDS = frozenset({
    "stream-events-policy",
    "stream-raw-events-policy",
    "stream-findings-policy",
    "stream-metrics-policy",
    "stream-active-responses-policy",
    "ai-assistant-sessions-policy",
})
POLICY_PREFIX = "tnwks-stream-"
AGE = re.compile(r"[1-9][0-9]*d")
RETRY = {"count": 3, "backoff": "exponential", "delay": "1m"}
# The size and count of the shipped rollover, kept as a second condition.
SHIPPED_ROLLOVER = {"min_doc_count": 200000000, "min_primary_shard_size": "20gb"}
# The ISM API gives a rollover back with copy_alias, false by default
# (OpenSearch index-management, RolloverAction): the job writes it, so that a
# policy it wrote compares equal and is not written again at each run.
ROLLOVER_DEFAULTS = {"copy_alias": False}
# The streams that hold no document: raw events are off, and no response can run.
EMPTY_STREAMS = ("wazuh-events-raw-v5", "wazuh-active-responses")
SOURCE = "wazuh-settings"


def check_retention(cfg: dict) -> None:
    for key in ("rollover_age", "delete_age", "metrics_delete_age"):
        if not AGE.fullmatch(str(cfg[key])):
            raise Stop(f"{key}: an age is a whole number of days, as 30d")
    for stream, spec in cfg["streams"].items():
        if spec["delete"] not in ("delete_age", "metrics_delete_age"):
            raise Stop(f"stream {stream}: unknown delete age {spec['delete']}")
        if not spec["patterns"][0].startswith(".ds-"):
            raise Stop(f"stream {stream}: the first pattern must be the one of the backing indices")
        if POLICY_PREFIX + stream in SHIPPED_POLICY_IDS:
            raise Stop(f"{POLICY_PREFIX + stream} is a shipped policy ID")


def build_policies(cfg: dict) -> dict:
    """The ISM policy of each stream, by policy ID."""
    policies = {}
    for stream, spec in cfg["streams"].items():
        delete_age = cfg[spec["delete"]]
        policies[POLICY_PREFIX + stream] = {
            "description": (f"tnwks: {stream}. Rolls over at {cfg['rollover_age']} (or at the shipped size or "
                            f"count), and deletes a backing index at {delete_age} from its creation."),
            "default_state": "hot",
            "states": [
                {"name": "hot",
                 "actions": [{"retry": RETRY, "rollover": {"min_index_age": cfg["rollover_age"],
                                                           **SHIPPED_ROLLOVER, **ROLLOVER_DEFAULTS}}],
                 "transitions": [{"state_name": "delete", "conditions": {"min_index_age": delete_age}}]},
                {"name": "delete", "actions": [{"retry": RETRY, "delete": {}}], "transitions": []},
            ],
            "ism_template": [{"index_patterns": list(spec["patterns"]), "priority": cfg["priority"]}],
        }
    return policies


def wait_for_login(client: Indexer, wait: float, log, sleep=time.sleep, clock=time.monotonic) -> None:
    end = clock() + wait
    while True:
        try:
            status, _ = client.call("GET", "/_plugins/_security/authinfo", ok=None)
        except NoAnswer as error:
            status, reason = None, str(error)
        else:
            if status == 200:
                return
            if status not in (401, 503):
                raise Stop(f"GET /_plugins/_security/authinfo as {client.user}: HTTP {status}")
            reason = f"HTTP {status}"

        remaining = end - clock()
        if status == 401:
            if remaining <= 0:
                raise Stop(f"{client.user} can not log in: step 1 has not made it, or its hash in "
                           "wazuh-accounts-hashes is not the one of the password in wazuh-settings-user")
            log(f"{client.user} can not log in yet: waits for step 1")
        else:
            if remaining <= 0:
                raise Stop(f"the indexer is not ready after {int(wait)} seconds: {reason}")
            log(f"the indexer is not ready yet ({reason}): waits")
        sleep(min(15, remaining))


def _comparable(policy: dict) -> dict:
    """The fields that the job writes, as the API gives them back."""
    templates = [{"index_patterns": t.get("index_patterns"), "priority": t.get("priority")}
                 for t in policy.get("ism_template") or []]
    return {"description": policy.get("description"), "default_state": policy.get("default_state"),
            "states": policy.get("states"), "ism_template": templates}


def apply_policies(client: Indexer, policies: dict, log) -> dict:
    """Write each policy that is missing or differs. Returns the version of each,
    as (seq_no, primary_term)."""
    versions = {}
    for policy_id, policy in policies.items():
        path = f"/_plugins/_ism/policies/{q(policy_id)}"
        status, data = client.call("GET", path, ok=(200, 404))
        if status == 200 and _comparable(data.get("policy") or {}) == _comparable(policy):
            versions[policy_id] = (data["_seq_no"], data["_primary_term"])
            log(f"policy {policy_id}: unchanged")
            continue
        body = {"policy": policy}
        if status == 200:
            path += f"?if_seq_no={data['_seq_no']}&if_primary_term={data['_primary_term']}"
        _, written = client.call("PUT", path, body)
        versions[policy_id] = (written["_seq_no"], written["_primary_term"])
        log(f"policy {policy_id}: {'updated' if status == 200 else 'created'}")
    return versions


def apply_raw_events(client: Indexer, cfg: dict, log) -> None:
    value = bool(cfg["index_raw_events"])
    client.call("PUT", "/_plugins/_setup/settings", {"engine": {"index_raw_events": value}})
    log(f"setting engine.index_raw_events: {str(value).lower()}")


def step_failed(info: dict) -> bool:
    """Count a stopped policy or exhausted action as failed.

    A failed step is retryable when ISM explicitly reports that both the
    action and the policy have not failed. Missing retry state fails closed.
    """
    step, action, retry = (info.get(key) or {} for key in ("step", "action", "retry_info"))
    if action.get("failed") is True or retry.get("failed") is True:
        return True
    if step.get("step_status") != "failed":
        return False
    return action.get("failed") is not False or retry.get("failed") is not False


def explain(client: Indexer, patterns) -> dict:
    """Each index of the patterns, with the policy and version that ISM reports
    for it, and whether its policy stopped at a failed step:
    {index: (policy_id or None, seq_no, primary_term, failed)}."""
    status, data = client.call("GET", f"/_plugins/_ism/explain/{q(','.join(patterns))}", ok=(200, 404))
    found = {}
    for index, info in (data or {}).items() if status == 200 else ():
        if not isinstance(info, dict):
            continue  # total_managed_indices
        policy_id = info.get("policy_id") or info.get("index.plugins.index_state_management.policy_id")
        found[index] = (policy_id, info.get("policy_seq_no"), info.get("policy_primary_term"), step_failed(info))
    return found


def _to_move(found: dict, policy_id: str, version: tuple) -> dict:
    """The indices that are not on the version of the policy. When ISM gives no
    version for an index, its policy ID alone decides."""
    moves = {}
    for index, (current, seq_no, primary_term, _) in found.items():
        same = current == policy_id and (seq_no is None or (seq_no, primary_term) == tuple(version))
        if not same:
            moves[index] = current
    return moves


def move_indices(client: Indexer, cfg: dict, versions: dict, log, wait: float, sleep=time.sleep,
                 clock=time.monotonic) -> list:
    """Put each index on the current version of its policy, and wait until ISM
    reports it. Returns the streams whose move did not apply in time."""
    pending = {}
    for stream, spec in cfg["streams"].items():
        policy_id = POLICY_PREFIX + stream
        moves = _to_move(explain(client, spec["patterns"]), policy_id, versions[policy_id])
        for index, current in sorted(moves.items()):
            action = "add" if current is None else "change_policy"
            _, data = client.call("POST", f"/_plugins/_ism/{action}/{q(index)}", {"policy_id": policy_id})
            if (data or {}).get("failures"):
                raise Stop(f"{action} {index} to {policy_id}: refused")
            log(f"index {index}: {action} to {policy_id}")
        if moves:
            pending[stream] = (spec["patterns"], policy_id, versions[policy_id])
    end = clock() + wait
    while pending:
        for stream, (patterns, policy_id, version) in list(pending.items()):
            if not _to_move(explain(client, patterns), policy_id, version):
                log(f"stream {stream}: each index on {policy_id}")
                del pending[stream]
        if not pending or clock() >= end:
            break
        sleep(15)
    return sorted(pending)


def count_indices(client: Indexer, cfg: dict) -> dict:
    streams = {}
    for stream, spec in cfg["streams"].items():
        found = explain(client, spec["patterns"])
        streams[stream] = {
            "indices": len(found),
            "off_policy": sum(1 for policy_id, *_ in found.values() if policy_id != POLICY_PREFIX + stream),
            "no_policy": sum(1 for policy_id, *_ in found.values() if policy_id is None),
            "failed": sum(1 for *_, failed in found.values() if failed),
        }
    return streams


def count_documents(client: Indexer) -> dict:
    """The documents of each stream that must hold none, from the index stats
    (a count, never a document)."""
    counts = {}
    for name in EMPTY_STREAMS:
        status, data = client.call("GET", f"/{q(name)}/_stats/docs", ok=(200, 404))
        primaries = ((data or {}).get("_all") or {}).get("primaries") or {}
        counts[name] = int((primaries.get("docs") or {}).get("count", 0)) if status == 200 else 0
    return counts


def certificate_ends(indexer_url: str, ca_file: str) -> list:
    """The end, in seconds of the epoch, of the certificate that the indexer
    serves and of the CA of the server. Reloader restarts the indexer when
    either one renews (the annotations of ../../wazuh/ks.yaml)."""
    url = urllib.parse.urlsplit(indexer_url)
    context = tls_context(ca_file)
    try:
        with socket.create_connection((url.hostname, url.port or 443), timeout=30) as raw, \
                context.wrap_socket(raw, server_hostname=url.hostname) as tls:
            served = tls.getpeercert()
    except OSError as error:
        raise Stop(f"can not read the certificate of the indexer: {error}") from None
    return [ssl.cert_time_to_seconds(cert["notAfter"]) for cert in [served, *context.get_ca_certs()]]


def write_results(results: Path, document: dict, name="indices.json") -> Path:
    """Publish complete result files atomically for the converter."""
    directory = results / SOURCE
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    final = directory / name
    partial = directory / f"{name}.tmp"
    partial.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    partial.chmod(0o644)
    os.replace(partial, final)
    return final


def hours(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)h", value)
    if not match:
        raise argparse.ArgumentTypeError("hours, as 720h")
    return int(match.group(1))


def record_channel_count(results: Path, count: int, stamp: int) -> None:
    write_results(results, {"version": 1, "source": SOURCE, "time": stamp, "active_channels": count}, "channels.json")


def main(argv=None, send=None, sleep=time.sleep, clock=time.monotonic, now=time.time, ends=certificate_ends,
         out=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--indexer", required=True)
    parser.add_argument("--ca", default="")
    parser.add_argument("--retention", required=True, help="the path of retention.json")
    parser.add_argument("--user", default="tnwks-settings")
    parser.add_argument("--secret", required=True,
                        help="the folder of the Secret wazuh-settings-user: the file password")
    parser.add_argument("--results", required=True, help="the root of the volume of results")
    parser.add_argument("--login-wait", type=float, default=300, help="seconds to wait for the login")
    parser.add_argument("--wait", type=float, default=1200, help="seconds to wait for ISM")
    parser.add_argument("--renew-before", type=hours, default=720,
                        help="the renewBefore of the certificates of the server, as 720h")
    parser.add_argument("--margin", type=float, default=3600,
                        help="no move when a certificate renews within this many seconds")
    parser.add_argument("--notifications-enabled", type=Path, help="the findings notification switch")
    parser.add_argument("--monitor-cluster", help="Alertmanager cluster label")
    args = parser.parse_args(argv)
    if (args.notifications_enabled is None) != (args.monitor_cluster is None):
        parser.error("--notifications-enabled and --monitor-cluster must be supplied together")
    try:
        retention = json.loads(Path(args.retention).read_text())
        check_retention(retention)
        password = read_secret(Path(args.secret) / "password")
        if not password_ok(password):
            raise Stop(f"the password of {args.user} breaks the password rule (12 to 64 characters, "
                       "a lowercase and an uppercase letter, a digit and a symbol)")
        client = Indexer(args.indexer, args.user, password, send=send, ca_file=args.ca)
        wait_for_login(client, args.login_wait, out, sleep=sleep, clock=clock)
        if args.notifications_enabled is not None:
            switch = args.notifications_enabled.read_text().strip()
            if switch not in {"true", "false"}:
                raise Stop("invalid Wazuh notification setting")
            enabled = switch == "true"
            monitors.wait_for_channel(client, enabled, args.login_wait, sleep, clock)
        versions = apply_policies(client, build_policies(retention), out)
        apply_raw_events(client, retention, out)
        renewal = min(ends(args.indexer, args.ca)) - args.renew_before * 3600
        if now() >= renewal - args.margin:
            out("moves: none in this run, because a certificate of the server renews within "
                f"{int(args.margin)} seconds, and Reloader then restarts the indexer")
            pending, moves = [], "skipped"
        else:
            pending = move_indices(client, retention, versions, out, args.wait, sleep=sleep, clock=clock)
            moves = "pending" if pending else "applied"
        if args.notifications_enabled is not None:
            results = Path(args.results)
            lock_dir = results / SOURCE
            lock_dir.mkdir(parents=True, exist_ok=True)
            with (lock_dir / ".monitors.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                monitors.apply(
                    client, args.monitor_cluster, enabled, out,
                    report_count=lambda count: record_channel_count(results, count, int(now())),
                )
        streams = count_indices(client, retention)
        document = {
            "version": 1, "source": SOURCE, "time": int(now()), "moves": moves,
            "indices": sum(s["indices"] for s in streams.values()),
            "off_policy": sum(s["off_policy"] for s in streams.values()),
            "no_policy": sum(s["no_policy"] for s in streams.values()),
            "failed": sum(s["failed"] for s in streams.values()),
            "streams": streams,
            "documents": count_documents(client),
        }
        write_results(Path(args.results), document)
        out(f"wrote {SOURCE}/indices.json: {document['indices']} indices, {document['off_policy']} not on "
            f"their policy of tnwks, {document['no_policy']} with no policy, {document['failed']} at a failed "
            f"step, documents {document['documents']}")
        problems = []
        if pending:
            problems.append("ISM has not applied the move yet for " + ", ".join(pending)
                            + ": do not restart the indexer before a run ends well")
        failed = sorted(stream for stream, counts in streams.items() if counts["failed"])
        if failed:
            problems.append("ISM stopped at a failed step on indices of " + ", ".join(failed)
                            + ": read the info of the ISM explain API of each one, fix the cause, and retry them")
        if problems:
            raise Stop("; ".join(problems))
    except Stop as error:
        out(f"error: {error}")
        return 1
    out("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
