"""The API checks of the security registry: one run, one result file for the converter.

The CronJob of ../ runs this script each day on homelab-wsl-02. It uses the
Python standard library only. Each test reads, and none changes a setting:

  routeros-firmware  sce-rtr01 (router). The version that the router runs, from
                     the series mktxp_system_uptime of the Prometheus of
                     homelab-wsl, where mktxp reads the router. The newest
                     stable release, from the release feed of the vendor. The
                     feed holds the newest release only, so the job keeps each
                     newest release it saw, with its date, in
                     api-check/routeros-releases.json. It fails (1) when the
                     router runs an older version than a release of that list
                     that came out more than 14 days ago. A list that is there
                     and can not be read gives no result and stays as it is.
  tfc-mfa-required   tnwks-ops (terraform-cloud-organization). The
                     authentication policy of the organization. It fails (1)
                     when the policy is not two_factor_mandatory. It runs only
                     when the Secret of the token is there.

The script writes api-check/results.json on the volume of results, through
api-check/results.json.tmp, in the form of security/converter/README.md ("The
result file of the API checks"):

- A test that did not run has no entry, never a 0. Its system then gets no time,
  and SecurityResultLate fires after 36 hours.
- The list of accepted findings (the Secret security-accepted-findings,
  optional) is read as the Prowler job reads it: an entry of `api-check` for a
  test and a system whose end date has not passed sets that result to 0 with no
  finding. A list that can not be read stops the run, which writes nothing.
- No token and no answer of an API as it came goes into the file or the log.

The run exits 1 when a test that should run did not, so KubeJobFailed fires.
Tests: security/tests/test_api_checks.py.
"""

import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

SOURCE = "api-check"
UTC = datetime.UTC
LIMIT = 1 << 20  # The largest answer the script reads, in bytes.
TIMEOUT = 30

ROUTER = "sce-rtr01"
FEED = "https://cdn.mikrotik.com/routeros/latest-stable.rss"
GRACE_DAYS = 14
TFC_API = "https://app.terraform.io/api/v2"
TFC_ORGANIZATION = "tnwks-ops"


RELEASES = "routeros-releases.json"


class QueryError(Exception):
    """A query gave no answer that a test can trust."""


class UnreadableList(Exception):
    """The list of accepted findings can not be read at all."""


# --- The list of accepted findings, read as kubernetes/apps/security/prowler reads it.

FIELDS = frozenset({"source", "check", "system", "reason", "until"})
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
CHECK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise UnreadableList("a key is twice in one object")
    return dict(pairs)


def _why_skipped(entry: object) -> tuple[str | None, datetime.date | None]:
    if not isinstance(entry, dict):
        return "not an object", None
    if set(entry) != FIELDS:
        return "a field is missing, or a key is not a field", None
    if not all(isinstance(entry[field], str) for field in FIELDS):
        return "a value is not a string", None
    if not all(entry[field].strip() for field in FIELDS):
        return "a field is empty", None
    if not CHECK.fullmatch(entry["check"]):
        return "the check is not a check ID", None
    if not DATE.fullmatch(entry["until"]):
        return "the end is not a date YYYY-MM-DD", None
    try:
        return None, datetime.date.fromisoformat(entry["until"])
    except ValueError:
        return "the end is not a date YYYY-MM-DD", None


def accepted_tests(path: Path, today: datetime.date) -> tuple[set[tuple[str, str]], int]:
    """The (check, system) pairs of `api-check` that the list accepts today, and the
    number of entries skipped because they do not fit. The rules of the form are the
    ones of the Prowler job and the converter: one JSON array of objects with exactly
    the five string fields, read with json.loads. The log has the number of an entry
    and why, never a value of the list."""
    if not path.is_file():
        return set(), 0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, RecursionError) as error:
        raise UnreadableList(type(error).__name__) from None
    if not isinstance(entries, list):
        raise UnreadableList("not a JSON array")
    accepted = set()
    skipped = 0
    for index, entry in enumerate(entries, 1):
        why, until = _why_skipped(entry)
        if why:
            print(f"entry {index} of the list of accepted findings is skipped: {why}", flush=True)
            skipped += 1
            continue
        if entry["source"] == SOURCE and until >= today:
            accepted.add((entry["check"], entry["system"]))
    return accepted, skipped


# --- The calls.

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could carry the token to another host: refuse each one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "a redirect is refused", headers, fp)


OPENER = urllib.request.build_opener(_NoRedirect)


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """The status and the body of a GET, up to LIMIT bytes. A fault of the network is a
    QueryError; an HTTP status that is not 200 comes back as the status."""
    request = urllib.request.Request(url, headers={"User-Agent": "tnwks-api-checks", **(headers or {})})
    try:
        with OPENER.open(request, timeout=TIMEOUT) as answer:
            body = answer.read(LIMIT + 1)
            status = answer.status
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise QueryError(f"no answer from {urllib.parse.urlsplit(url).hostname}: {type(error).__name__}") from None
    if len(body) > LIMIT:
        raise QueryError(f"the answer of {urllib.parse.urlsplit(url).hostname} is larger than {LIMIT} bytes")
    return status, body


def version_key(text: str) -> tuple[int, ...]:
    """7.25.1 -> (7, 25, 1). The leading number of a label such as "7.25.1 (stable)"."""
    match = re.match(r"\s*([0-9]+(?:\.[0-9]+)*)", text)
    if not match:
        raise QueryError("not a version")
    return tuple(int(part) for part in match[1].split("."))


def running_version(prometheus: str, router: str, get=http_get) -> tuple[int, ...]:
    """The version of RouterOS that the router runs, from the label `version` of the
    series of mktxp. No series (mktxp did not read the router in the last 5
    minutes), or two versions, is a QueryError."""
    selector = f'mktxp_system_uptime{{routerboard_name="{router}"}}'
    url = f"{prometheus.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": f"count by (version) ({selector})"})
    status, body = get(url)
    if status != 200:
        raise QueryError(f"Prometheus answered {status}")
    try:
        data = json.loads(body)
        rows = data["data"]["result"] if data.get("status") == "success" else None
        versions = {row["metric"]["version"] for row in rows}
    except (ValueError, KeyError, TypeError) as error:
        raise QueryError(f"the answer of Prometheus does not fit: {type(error).__name__}") from None
    if len(versions) != 1:
        raise QueryError(f"Prometheus has {len(versions)} versions of the router, not one")
    return version_key(versions.pop())


def newest_release(get=http_get, feed: str = FEED) -> tuple[tuple[int, ...], datetime.datetime]:
    """The newest stable release of RouterOS and the time it came out, from the feed of
    the vendor: items titled "RouterOS <version> [stable]", with a pubDate."""
    status, body = get(feed)
    if status != 200:
        raise QueryError(f"the release feed answered {status}")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise QueryError("the release feed is not XML") from None
    releases = []
    for item in root.iter("item"):
        title = re.fullmatch(r"\s*RouterOS ([0-9.]+) \[stable\]\s*", item.findtext("title") or "")
        published = item.findtext("pubDate")
        if not title or not published:
            continue
        try:
            when = email.utils.parsedate_to_datetime(published.strip())
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            continue
        releases.append((version_key(title[1]), when))
    if not releases:
        raise QueryError("the release feed has no stable release with a date")
    return max(releases)


def read_releases(path: Path) -> dict[str, float]:
    """{version: the time it came out} of each newest release the job saw. A missing file
    is the first run. A file that is there and can not be read is a QueryError."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        seen = data["first_seen"]
        if data.get("version") != 1 or not isinstance(seen, dict):
            raise ValueError("not version 1")
        out = {}
        for version, moment in seen.items():
            version_key(version)
            if not isinstance(moment, (int, float)) or isinstance(moment, bool):
                raise TypeError("a time is not a number")
            out[version] = float(moment)
        return out
    except (OSError, ValueError, KeyError, TypeError, QueryError) as error:
        raise QueryError(f"{path.name} can not be read: {type(error).__name__}") from None


def firmware(running: tuple[int, ...], releases: dict[str, float], now: datetime.datetime,
             grace_days: int = GRACE_DAYS) -> list[str]:
    """The releases newer than the running version that came out more than the grace ago,
    newest first. The router fails when the list is not empty."""
    limit = now.timestamp() - grace_days * 86400
    late = [version for version, moment in releases.items() if version_key(version) > running and moment < limit]
    return sorted(late, key=version_key, reverse=True)


def tfc_policy(token: str, organization: str = TFC_ORGANIZATION, get=http_get) -> str:
    """The authentication policy of the organization. HCP Terraform answers 404 to a token
    that may not read it, and 401 to a token that is not valid."""
    status, body = get(f"{TFC_API}/organizations/{urllib.parse.quote(organization)}",
                       {"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"})
    if status != 200:
        raise QueryError(f"HCP Terraform answered {status}")
    try:
        policy = json.loads(body)["data"]["attributes"]["collaborator-auth-policy"]
    except (ValueError, KeyError, TypeError) as error:
        raise QueryError(f"the answer of HCP Terraform does not fit: {type(error).__name__}") from None
    if not isinstance(policy, str) or not policy:
        raise QueryError("the organization has no authentication policy")
    return policy


# --- The run.

def show(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def tests(args: argparse.Namespace, now: datetime.datetime, get=http_get) -> tuple[list[dict], list[str]]:
    """The entry of each test that ran, and the names of the tests that should have run
    and did not."""
    results: list[dict] = []
    missed: list[str] = []

    try:
        path = args.results / SOURCE / RELEASES
        releases = read_releases(path)
        newest, published = newest_release(get, args.feed)
        if show(newest) not in releases:
            # The date of the feed, or now if the feed gives a later one.
            releases[show(newest)] = min(published.timestamp(), now.timestamp())
            write_json(args.results, RELEASES, {"version": 1, "first_seen": releases})
        running = running_version(args.prometheus, args.router, get)
        late = firmware(running, releases, now, args.grace_days)
        entry = {"check": "routeros-firmware", "system": args.router, "failed": int(bool(late)), "severity": "high"}
        if late:
            out = datetime.datetime.fromtimestamp(releases[late[0]], UTC).date().isoformat()
            entry["findings"] = [{
                "resource": args.router,
                "message": f"RouterOS {show(running)} runs. The stable release {late[0]} came out on {out}.",
            }]
        results.append(entry)
    except QueryError as error:
        print(f"routeros-firmware for {args.router}: no result ({error})", flush=True)
        missed.append("routeros-firmware")

    token = os.environ.get("TFE_TOKEN", "").strip()
    if not token:
        print(f"tfc-mfa-required for {args.tfc_organization}: no token, so the test does not run", flush=True)
    else:
        try:
            policy = tfc_policy(token, args.tfc_organization, get)
            failed = int(policy != "two_factor_mandatory")
            entry = {"check": "tfc-mfa-required", "system": args.tfc_organization, "failed": failed,
                     "severity": "medium"}
            if failed:
                entry["findings"] = [{
                    "resource": args.tfc_organization,
                    "message": f"The authentication policy of the organization is {policy[:40]},"
                               " not two_factor_mandatory.",
                }]
            results.append(entry)
        except QueryError as error:
            print(f"tfc-mfa-required for {args.tfc_organization}: no result ({error})", flush=True)
            missed.append("tfc-mfa-required")
    return results, missed


def apply_accepted(results: list[dict], accepted: set[tuple[str, str]]) -> list[dict]:
    """An accepted test counts no resource and lists no finding."""
    out = []
    for entry in results:
        if (entry["check"], entry["system"]) in accepted and entry["failed"]:
            entry = {key: value for key, value in entry.items() if key != "findings"} | {"failed": 0}
        out.append(entry)
    return out


def write_json(results_dir: Path, name: str, document: dict) -> Path:
    """api-check/<name>, through <name>.tmp: one move, so no reader sees half a file.
    Mode 0755 and 0644, so the converter (another user) reads it."""
    directory = results_dir / SOURCE
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    final = directory / name
    partial = directory / f"{name}.tmp"
    partial.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    partial.chmod(0o644)
    os.replace(partial, final)
    return final


def run(args: argparse.Namespace, now=lambda: datetime.datetime.now(UTC), get=http_get) -> int:
    started = now()
    try:
        accepted, skipped = accepted_tests(args.accepted, started.date())
    except UnreadableList as error:
        print(f"the list of accepted findings can not be read ({error}): no run", flush=True)
        return 2
    results, missed = tests(args, started, get)
    results = apply_accepted(results, accepted)
    document = {"version": 1, "source": SOURCE, "time": int(now().timestamp()), "results": results}
    final = write_json(args.results, "results.json", document)
    print(f"wrote {final.relative_to(args.results)}: {len(results)} results, {len(accepted)} accepted tests,"
          f" {skipped} entries of the list skipped", flush=True)
    return 1 if missed else 0


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus", required=True, help="the Prometheus where mktxp reads the router")
    parser.add_argument("--router", default=ROUTER)
    parser.add_argument("--feed", default=FEED)
    parser.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    parser.add_argument("--tfc-organization", default=TFC_ORGANIZATION)
    parser.add_argument("--accepted", type=Path, default=Path("/accepted/accepted.json"))
    parser.add_argument("--results", type=Path, default=Path("/results"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse(sys.argv[1:])))
