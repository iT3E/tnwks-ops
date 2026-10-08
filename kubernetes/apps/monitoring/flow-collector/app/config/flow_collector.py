#!/usr/bin/env python3
"""WAN flow collector: NetFlow v5/v9/IPFIX in, per-client Prometheus counters out.

Router-agnostic on purpose: VyOS (flow-accounting) and RouterOS (traffic-flow)
both speak IPFIX, so swapping routers only changes where the flows come from.

For every flow with exactly one private (LAN) endpoint:
  client  = the private endpoint
  remote  = the public endpoint, labelled with the network that owns it (ASN)
  LAN -> internet bytes count as transmit (upload), internet -> LAN as receive.
Flows with two private endpoints (inter-VLAN) or none are ignored.

Exposed on :9300/metrics:
  wan_client_transmit_bytes_total{client_ip, asn, as_name}
  wan_client_receive_bytes_total{client_ip, asn, as_name}
  flow_collector_* self-metrics

Stdlib only, so it runs on a stock python image with this file mounted.
"""

import gzip
import ipaddress
import os
import socket
import struct
import sys
import threading
import time
import urllib.request
from array import array
from bisect import bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT = int(os.environ.get("FLOW_PORT", "2055"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9300"))
LAN_NETS = [ipaddress.ip_network(n.strip()) for n in os.environ.get(
    "LAN_NETS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16").split(",") if n.strip()]
ASN_URL = os.environ.get("ASN_DB_URL", "https://iptoasn.com/data/ip2asn-v4.tsv.gz")
ASN_CACHE = os.environ.get("ASN_DB_CACHE", "/cache/ip2asn-v4.tsv.gz")
ASN_REFRESH_S = int(os.environ.get("ASN_DB_REFRESH_SECONDS", str(7 * 86400)))
SERIES_TTL_S = int(os.environ.get("SERIES_TTL_SECONDS", "3600"))

LOCK = threading.Lock()
# (direction, client_ip, asn) -> [bytes, last_seen]
COUNTERS = {}
STATS = {
    "packets": {},             # version -> count
    "records": 0,
    "records_lan_wan": 0,
    "templates": 0,
    "missing_template": 0,
    "decode_errors": 0,
    "last_packet": 0.0,
}
ASN = {"starts": array("I"), "ends": array("I"), "asns": array("I"),
       "names": {}, "loaded_at": 0.0}


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- ASN lookup

def _short_name(desc):
    # iptoasn: "VALVE-CORPORATION Valve Corporation" / "BACKBLAZE" / "Not routed"
    handle, _, org = desc.partition(" ")
    name = org.strip() or handle
    for suffix in (", Inc.", " Inc.", ", LLC", " LLC", " Corporation", " Corp.", " Ltd.", " Limited"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip(" ,")[:40] or handle


def load_asn_db(path):
    starts, ends, asns, names = array("I"), array("I"), array("I"), {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            asn = int(parts[2])
            if asn == 0:
                continue  # "Not routed"
            starts.append(int(ipaddress.IPv4Address(parts[0])))
            ends.append(int(ipaddress.IPv4Address(parts[1])))
            asns.append(asn)
            if asn not in names:
                names[asn] = _short_name(parts[4])
    return starts, ends, asns, names


def refresh_asn_db():
    path = ASN_CACHE
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < ASN_REFRESH_S
    if not fresh:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            req = urllib.request.Request(ASN_URL, headers={"User-Agent": "tnwks-flow-collector"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(tmp, path)
            log(f"asn db downloaded from {ASN_URL}")
        except Exception as exc:  # keep the old table if the download fails
            log(f"asn db download failed: {exc!r}")
            if not os.path.exists(path):
                return False
    try:
        starts, ends, asns, names = load_asn_db(path)
    except Exception as exc:
        log(f"asn db load failed: {exc!r}")
        return False
    with LOCK:
        ASN.update(starts=starts, ends=ends, asns=asns, names=names, loaded_at=time.time())
    log(f"asn db loaded: {len(starts)} ranges, {len(names)} networks")
    return True


def asn_loop():
    while True:
        ok = refresh_asn_db()
        time.sleep(ASN_REFRESH_S if ok else 3600)


def lookup_asn(ip_int):
    starts = ASN["starts"]
    i = bisect_right(starts, ip_int) - 1
    if i >= 0 and ip_int <= ASN["ends"][i]:
        asn = ASN["asns"][i]
        return asn, ASN["names"].get(asn, f"AS{asn}")
    return 0, "unknown"


# ---------------------------------------------------------------- accounting

def is_lan(ip_int):
    ip = ipaddress.IPv4Address(ip_int)
    return any(ip in n for n in LAN_NETS)


def account(src, dst, nbytes, now):
    if not nbytes:
        return
    s_lan, d_lan = is_lan(src), is_lan(dst)
    if s_lan == d_lan:
        return  # inter-VLAN, or not involving the LAN at all
    if s_lan:
        direction, client, remote = "transmit", src, dst
    else:
        direction, client, remote = "receive", dst, src
    asn, _ = lookup_asn(remote)
    key = (direction, client, asn)
    with LOCK:
        STATS["records_lan_wan"] += 1
        c = COUNTERS.get(key)
        if c is None:
            COUNTERS[key] = [nbytes, now]
        else:
            c[0] += nbytes
            c[1] = now


# ---------------------------------------------------------------- decoding
# IANA IPFIX / NetFlow v9 field ids we use
F_OCTETS = (1, 85)          # octetDeltaCount, octetTotalCount
F_SRC4, F_DST4 = 8, 12      # source/destinationIPv4Address
F_OUT_BYTES = 23            # NetFlow v9 OUT_BYTES / postOctetDeltaCount

TEMPLATES = {}  # (version, domain, template_id) -> list[(field_id, length)]


def _uint(b):
    return int.from_bytes(b, "big")


def _parse_template_set(version, domain, body, ipfix):
    off = 0
    while off + 4 <= len(body):
        tid, count = struct.unpack_from("!HH", body, off)
        off += 4
        if tid < 256:
            break  # padding
        fields = []
        for _ in range(count):
            ftype, flen = struct.unpack_from("!HH", body, off)
            off += 4
            if ipfix and ftype & 0x8000:
                off += 4  # enterprise number: not one of ours
                ftype = 0x8000
            fields.append((ftype, flen))
        TEMPLATES[(version, domain, tid)] = fields
        with LOCK:
            STATS["templates"] = len(TEMPLATES)


def _parse_data_set(version, domain, tid, body, now):
    fields = TEMPLATES.get((version, domain, tid))
    if fields is None:
        with LOCK:
            STATS["missing_template"] += 1
        return
    fixed = all(l != 0xFFFF for _, l in fields)
    # Smallest possible record; anything shorter left in the set is padding.
    min_len = sum(1 if l == 0xFFFF else l for _, l in fields)
    off = 0
    n = 0
    while off + min_len <= len(body) and min_len > 0:
        src = dst = None
        octets = 0
        start = off
        try:
            for ftype, flen in fields:
                if flen == 0xFFFF:  # IPFIX variable length
                    flen = body[off]
                    off += 1
                    if flen == 255:
                        flen = struct.unpack_from("!H", body, off)[0]
                        off += 2
                val = body[off:off + flen]
                off += flen
                if ftype == F_SRC4 and flen == 4:
                    src = _uint(val)
                elif ftype == F_DST4 and flen == 4:
                    dst = _uint(val)
                elif ftype in F_OCTETS or (ftype == F_OUT_BYTES and not octets):
                    octets = max(octets, _uint(val))
        except (IndexError, struct.error):
            break  # truncated record / trailing padding
        if off == start or off > len(body):
            break
        if not fixed and src is None and dst is None and not octets:
            break  # zero padding parsed as a variable-length record
        n += 1
        if src is not None and dst is not None:
            account(src, dst, octets, now)
    with LOCK:
        STATS["records"] += n


def handle_packet(data, now):
    if len(data) < 4:
        return
    version = struct.unpack_from("!H", data, 0)[0]
    with LOCK:
        STATS["packets"][version] = STATS["packets"].get(version, 0) + 1
        STATS["last_packet"] = now
    if version == 5:
        count = struct.unpack_from("!H", data, 2)[0]
        for i in range(count):
            off = 24 + i * 48
            if off + 48 > len(data):
                break
            src, dst = struct.unpack_from("!II", data, off)
            octets = struct.unpack_from("!I", data, off + 20)[0]
            account(src, dst, octets, now)
        with LOCK:
            STATS["records"] += count
        return
    if version == 9:
        domain = struct.unpack_from("!I", data, 16)[0]
        off, end, ipfix = 20, len(data), False
    elif version == 10:
        length, _, _, domain = struct.unpack_from("!HIII", data, 2)
        off, end, ipfix = 16, min(length, len(data)), True
    else:
        return
    while off + 4 <= end:
        set_id, set_len = struct.unpack_from("!HH", data, off)
        if set_len < 4:
            break
        body = data[off + 4: off + set_len]
        if set_id in (0, 2):        # v9 / IPFIX template set
            _parse_template_set(version, domain, body, ipfix)
        elif set_id in (1, 3):      # options templates: not needed
            pass
        elif set_id >= 256:
            _parse_data_set(version, domain, set_id, body, now)
        off += set_len


def flow_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    log(f"listening for NetFlow/IPFIX on udp/{LISTEN_PORT}")
    while True:
        data, _ = sock.recvfrom(65535)
        try:
            handle_packet(data, time.time())
        except Exception as exc:
            with LOCK:
                STATS["decode_errors"] += 1
            log(f"decode error: {exc!r}")


# ---------------------------------------------------------------- exposition

def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ")


def render():
    now = time.time()
    lines = []
    with LOCK:
        for key in [k for k, v in COUNTERS.items() if now - v[1] > SERIES_TTL_S]:
            del COUNTERS[key]
        rows = sorted(COUNTERS.items())
        names = ASN["names"]
        stats = {k: (dict(v) if isinstance(v, dict) else v) for k, v in STATS.items()}
        asn_entries = len(ASN["starts"])
        asn_loaded = ASN["loaded_at"]
    for direction in ("transmit", "receive"):
        metric = f"wan_client_{direction}_bytes_total"
        lines.append(f"# HELP {metric} Bytes between a LAN client and the internet "
                     f"({'upload' if direction == 'transmit' else 'download'}), by remote network.")
        lines.append(f"# TYPE {metric} counter")
        for (d, client, asn), (nbytes, _) in rows:
            if d != direction:
                continue
            as_name = names.get(asn, "unknown") if asn else "unknown"
            lines.append(f'{metric}{{client_ip="{ipaddress.IPv4Address(client)}",asn="{asn}",'
                         f'as_name="{_esc(as_name)}"}} {nbytes}')
    lines.append("# TYPE flow_collector_packets_total counter")
    for v, c in sorted(stats["packets"].items()):
        lines.append(f'flow_collector_packets_total{{version="{v}"}} {c}')
    for name, help_ in (("records", "Flow records decoded"),
                        ("records_lan_wan", "Flow records between the LAN and the internet"),
                        ("missing_template", "Data sets dropped because no template was seen yet"),
                        ("decode_errors", "Packets that failed to decode")):
        lines.append(f"# HELP flow_collector_{name}_total {help_}")
        lines.append(f"# TYPE flow_collector_{name}_total counter")
        lines.append(f"flow_collector_{name}_total {stats[name]}")
    lines.append("# TYPE flow_collector_templates gauge")
    lines.append(f"flow_collector_templates {stats['templates']}")
    lines.append("# TYPE flow_collector_last_packet_timestamp_seconds gauge")
    lines.append(f"flow_collector_last_packet_timestamp_seconds {stats['last_packet']}")
    lines.append("# TYPE flow_collector_asn_db_ranges gauge")
    lines.append(f"flow_collector_asn_db_ranges {asn_entries}")
    lines.append("# TYPE flow_collector_asn_db_loaded_timestamp_seconds gauge")
    lines.append(f"flow_collector_asn_db_loaded_timestamp_seconds {asn_loaded}")
    lines.append("# TYPE flow_collector_series gauge")
    lines.append(f"flow_collector_series {len(rows)}")
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body, ctype = render(), "text/plain; version=0.0.4"
        elif self.path.startswith("/healthz"):
            body, ctype = b"ok\n", "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    threading.Thread(target=asn_loop, daemon=True, name="asn").start()
    threading.Thread(target=flow_loop, daemon=True, name="flows").start()
    log(f"metrics on :{METRICS_PORT}/metrics, LAN nets {', '.join(map(str, LAN_NETS))}")
    ThreadingHTTPServer(("0.0.0.0", METRICS_PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
