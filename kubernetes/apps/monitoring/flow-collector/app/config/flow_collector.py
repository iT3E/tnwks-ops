#!/usr/bin/env python3
"""WAN flow collector: sFlow v5 / NetFlow v5/v9/IPFIX in, per-client Prometheus counters out.

Router-agnostic on purpose: VyOS (flow-accounting) and RouterOS (traffic-flow)
both speak IPFIX, so swapping routers only changes where the flows come from.

For every flow with exactly one private (LAN) endpoint:
  client  = the private endpoint
  remote  = the public endpoint, labelled with the network that owns it (ASN)
  LAN -> internet bytes count as transmit (upload), internet -> LAN as receive.
Flows with two private endpoints (inter-VLAN) or none are ignored.

Exported on the WAN port (RouterOS traffic-flow on ether1), a download is
recorded as it arrives, before NAT is undone: its destination is the router's
public address. RouterOS adds postNATDestinationIPv4Address (field 226) with
the LAN client, so a public -> public flow whose post-NAT destination is
private counts as a receive for that client. Uploads already carry the LAN
source (pre-NAT), so they need nothing extra.

Each flow is also classified (see classify()):
  service  = the remote end's protocol/port (https, quic, dns, stun, ...)
  app      = best guess at the application (Netflix, Steam, DNS, Web, ...)
  category = a small fixed set (Streaming, Gaming, Calls, Cloud & CDN, ...)
from a port table and a table of networks that belong to one service.
It is a heuristic: most traffic is HTTPS on 443, and CDN/cloud networks
(AWS, Fastly, Akamai, Cloudflare) carry many apps, so those keep the
provider's name and the "Cloud & CDN" category.

Exposed on :9300/metrics:
  wan_client_transmit_bytes_total{client_ip, asn, as_name, service, app, category}
  wan_client_receive_bytes_total{client_ip, asn, as_name, service, app, category}
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
# (direction, client_ip, asn, service) -> [bytes, last_seen]
COUNTERS = {}
STATS = {
    "packets": {},             # version -> count
    "records": 0,
    "records_lan_wan": 0,
    "records_post_nat": 0,
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


# ---------------------------------------------------------------- classification
# Remote (proto, port) -> service. Ranges are inclusive.
PROTO_NAMES = {1: "icmp", 6: "tcp", 17: "udp", 47: "gre", 50: "esp", 58: "icmpv6"}
_SERVICE_RANGES = [
    ("tcp", 443, 443, "https"), ("udp", 443, 443, "quic"), ("tcp", 80, 80, "http"),
    ("tcp", 8443, 8443, "https-alt"), ("tcp", 8080, 8080, "http-alt"),
    ("udp", 53, 53, "dns"), ("tcp", 53, 53, "dns"),
    ("tcp", 853, 853, "dns-over-tls"), ("udp", 853, 853, "dns-over-quic"),
    ("udp", 123, 123, "ntp"),
    ("udp", 3478, 3481, "stun"), ("tcp", 3478, 3478, "stun"), ("udp", 19302, 19309, "stun"),
    ("udp", 51820, 51820, "wireguard"), ("udp", 500, 500, "ipsec"), ("udp", 4500, 4500, "ipsec"),
    ("udp", 1194, 1194, "openvpn"), ("tcp", 1194, 1194, "openvpn"),
    ("tcp", 22, 22, "ssh"), ("tcp", 3389, 3389, "rdp"), ("udp", 3389, 3389, "rdp"),
    ("tcp", 25, 25, "smtp"), ("tcp", 465, 465, "smtp"), ("tcp", 587, 587, "smtp"),
    ("tcp", 143, 143, "imap"), ("tcp", 993, 993, "imap"),
    ("tcp", 110, 110, "pop3"), ("tcp", 995, 995, "pop3"),
    ("tcp", 5228, 5230, "google-push"), ("tcp", 5223, 5223, "apple-push"),
    ("tcp", 1883, 1883, "mqtt"), ("tcp", 8883, 8883, "mqtt"),
    ("tcp", 554, 554, "rtsp"), ("udp", 554, 554, "rtsp"), ("tcp", 1935, 1935, "rtmp"),
    ("udp", 8801, 8810, "zoom"), ("udp", 9000, 9000, "webex"),
    ("udp", 27000, 27100, "steam"), ("udp", 3074, 3074, "xbox-live"),
]
# Services that say what the traffic is regardless of who is on the other end.
STRONG_SERVICES = {
    "dns": ("DNS", "Infrastructure"), "dns-over-tls": ("DNS", "Infrastructure"),
    "dns-over-quic": ("DNS", "Infrastructure"), "ntp": ("NTP", "Infrastructure"),
    "wireguard": ("VPN", "VPN & security"), "ipsec": ("VPN", "VPN & security"),
    "openvpn": ("VPN", "VPN & security"), "esp": ("VPN", "VPN & security"),
    "gre": ("VPN", "VPN & security"),
    "ssh": ("SSH", "Remote access"), "rdp": ("RDP", "Remote access"),
    "smtp": ("Email", "Email"), "imap": ("Email", "Email"), "pop3": ("Email", "Email"),
    "google-push": ("Google push", "Notifications"), "apple-push": ("Apple push", "Notifications"),
    "mqtt": ("MQTT", "IoT"), "rtsp": ("RTSP", "IoT"),
    "zoom": ("Zoom", "Calls"),
}
# Services that only win when the network doesn't already name the app.
WEAK_SERVICES = {
    "stun": ("WebRTC", "Calls"), "webex": ("Webex", "Calls"), "rtmp": ("RTMP", "Streaming"),
    "steam": ("Steam", "Gaming"), "xbox-live": ("Xbox Live", "Gaming"),
}
WEB_SERVICES = {"https", "quic", "http", "https-alt", "http-alt"}

# Networks (ASNs) -> (app, category). HOSTING networks carry many unrelated
# apps, so a port rule beats them and they keep the provider's name.
ASN_APPS = {
    # Streaming
    2906: ("Netflix", "Streaming"), 40027: ("Netflix", "Streaming"), 55095: ("Netflix", "Streaming"),
    43515: ("YouTube", "Streaming"), 36040: ("YouTube", "Streaming"),
    46489: ("Twitch", "Streaming"), 23286: ("Hulu", "Streaming"), 8403: ("Spotify", "Streaming"),
    # Social
    32934: ("Meta", "Social"), 63293: ("Meta", "Social"), 54115: ("Meta", "Social"),
    13414: ("X", "Social"), 35995: ("X", "Social"),
    396986: ("TikTok", "Social"), 138699: ("TikTok", "Social"), 14413: ("LinkedIn", "Social"),
    # Messaging / calls
    62041: ("Telegram", "Messaging"), 59930: ("Telegram", "Messaging"), 62014: ("Telegram", "Messaging"),
    44907: ("Telegram", "Messaging"), 211157: ("Telegram", "Messaging"),
    13445: ("Webex", "Calls"), 30103: ("Zoom", "Calls"),
    # Gaming
    32590: ("Steam", "Gaming"), 57976: ("Blizzard", "Gaming"), 6507: ("Riot Games", "Gaming"),
    22697: ("Roblox", "Gaming"),
    # Big platforms: too mixed to split further from flows alone
    15169: ("Google", "Google"), 19527: ("Google", "Google"),
    8075: ("Microsoft", "Microsoft"), 8068: ("Microsoft", "Microsoft"), 8069: ("Microsoft", "Microsoft"),
    714: ("Apple", "Apple"), 6185: ("Apple", "Apple"),
    # Work / security proxies (traffic inside the tunnel is invisible)
    55256: ("Netskope", "VPN & security"), 36692: ("Cisco Umbrella", "VPN & security"),
    22616: ("Zscaler", "VPN & security"), 53813: ("Zscaler", "VPN & security"),
    62044: ("Zscaler", "VPN & security"),
    # Other single-purpose networks
    399358: ("Anthropic", "AI"), 36459: ("GitHub", "Dev"), 41231: ("Canonical", "Dev"),
    394161: ("Tesla", "IoT"), 19281: ("Quad9", "Infrastructure"),
    198622: ("Ads", "Ads & tracking"), 19750: ("Ads", "Ads & tracking"), 26667: ("Ads", "Ads & tracking"),
    62713: ("Ads", "Ads & tracking"), 29990: ("Ads", "Ads & tracking"), 23467: ("New Relic", "Ads & tracking"),
}
HOSTING = {
    16509: "AWS", 14618: "AWS", 13335: "Cloudflare", 209242: "Cloudflare", 54113: "Fastly",
    20940: "Akamai", 16625: "Akamai", 12222: "Akamai", 63949: "Akamai", 396982: "Google Cloud",
    20473: "Vultr", 14061: "DigitalOcean", 16276: "OVH", 24940: "Hetzner", 31898: "Oracle Cloud",
    15133: "Edgio", 22822: "Edgio", 19551: "Imperva", 199524: "Gcore", 45102: "Alibaba Cloud",
    37963: "Alibaba Cloud",
}


def service_of(proto, remote_port, client_port):
    """Name the remote end's service; fall back to a well-known client port (inbound)."""
    pname = PROTO_NAMES.get(proto, "other")
    if pname not in ("tcp", "udp"):
        return pname
    for port in (remote_port, client_port):
        for p, lo, hi, name in _SERVICE_RANGES:
            if p == pname and lo <= port <= hi:
                return name
    return f"{pname}-other"


_CLASS_CACHE = {}


def classify(asn, service):
    """(asn, service) -> (app, category). Order: strong port, network, weak port, host, web."""
    hit = _CLASS_CACHE.get((asn, service))
    if hit:
        return hit
    if service in STRONG_SERVICES:
        res = STRONG_SERVICES[service]
    elif asn in ASN_APPS:
        app, cat = ASN_APPS[asn]
        res = (app, "Calls") if service == "stun" else (app, cat)
    elif service in WEAK_SERVICES:
        res = WEAK_SERVICES[service]
    elif asn in HOSTING:
        res = (HOSTING[asn], "Cloud & CDN")
    elif service in WEB_SERVICES:
        res = ("Web", "Web")
    else:
        res = ("Other", "Other")
    _CLASS_CACHE[(asn, service)] = res
    return res


# ---------------------------------------------------------------- accounting

def is_lan(ip_int):
    ip = ipaddress.IPv4Address(ip_int)
    return any(ip in n for n in LAN_NETS)


def account(src, dst, nbytes, now, post_nat_dst=None, proto=0, sport=0, dport=0):
    if not nbytes:
        return
    s_lan, d_lan = is_lan(src), is_lan(dst)
    if not s_lan and not d_lan and post_nat_dst is not None and is_lan(post_nat_dst):
        # A download seen on the WAN port before NAT is undone (see the docstring).
        dst, d_lan = post_nat_dst, True
        with LOCK:
            STATS["records_post_nat"] += 1
    if s_lan == d_lan:
        return  # inter-VLAN, or not involving the LAN at all
    if s_lan:
        direction, client, remote = "transmit", src, dst
        remote_port, client_port = dport, sport
    else:
        direction, client, remote = "receive", dst, src
        remote_port, client_port = sport, dport
    asn, _ = lookup_asn(remote)
    key = (direction, client, asn, service_of(proto, remote_port, client_port))
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
F_POST_NAT_DST4 = 226       # postNATDestinationIPv4Address (RouterOS, NetFlow v9 + IPFIX)
F_OUT_BYTES = 23            # NetFlow v9 OUT_BYTES / postOctetDeltaCount
F_PROTO = 4                 # protocolIdentifier
F_SPORT, F_DPORT = 7, 11    # source/destinationTransportPort

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
        src = dst = post_nat_dst = None
        octets = proto = sport = dport = 0
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
                elif ftype == F_POST_NAT_DST4 and flen == 4:
                    post_nat_dst = _uint(val)
                elif ftype == F_PROTO:
                    proto = _uint(val)
                elif ftype == F_SPORT:
                    sport = _uint(val)
                elif ftype == F_DPORT:
                    dport = _uint(val)
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
            account(src, dst, octets, now, post_nat_dst, proto, sport, dport)
    with LOCK:
        STATS["records"] += n


def _account_ip_header(h, nbytes, now):
    """h starts at an IPv4 header; account its src/dst."""
    if len(h) < 20 or h[0] >> 4 != 4:
        return False
    src = struct.unpack_from("!I", h, 12)[0]
    dst = struct.unpack_from("!I", h, 16)[0]
    proto, ihl = h[9], (h[0] & 0xF) * 4
    sport = dport = 0
    if proto in (6, 17) and len(h) >= ihl + 4:
        sport, dport = struct.unpack_from("!HH", h, ihl)
    account(src, dst, nbytes, now, None, proto, sport, dport)
    return True


def _account_ether_header(h, nbytes, now):
    """h starts at an Ethernet header (802.1Q/802.1ad tags allowed)."""
    if len(h) < 14:
        return False
    off = 12
    etype = struct.unpack_from("!H", h, off)[0]
    while etype in (0x8100, 0x88A8) and off + 6 <= len(h):
        off += 4
        etype = struct.unpack_from("!H", h, off)[0]
    if etype != 0x0800:
        return False  # IPv6/ARP/...: VyOS doesn't forward IPv6 today
    return _account_ip_header(h[off + 2:], nbytes, now)


def handle_sflow(data, now):
    """sFlow v5: sampled packet headers, scaled by the sampling rate.

    VyOS 1.4's netflow exporter (pmacct uacctd) misreads addresses on VLAN
    interfaces, so VyOS uses sFlow (hsflowd) instead. Same port as NetFlow;
    told apart by the 32-bit version field (5).
    """
    u32 = lambda o: struct.unpack_from("!I", data, o)[0]
    addr_type = u32(4)
    off = 8 + (4 if addr_type == 1 else 16) + 12  # agent, sub-agent, seq, uptime
    nsamples = u32(off)
    off += 4
    n = 0
    for _ in range(nsamples):
        fmt, length = u32(off), u32(off + 4)
        body_off, off = off + 8, off + 8 + length
        enterprise, kind = fmt >> 12, fmt & 0xFFF
        if enterprise != 0 or kind not in (1, 3):
            continue  # counter samples etc.
        if kind == 1:   # flow_sample
            rate = u32(body_off + 8)
            rec_off = body_off + 32
            nrec = u32(body_off + 28)
        else:           # expanded flow_sample
            rate = u32(body_off + 12)
            rec_off = body_off + 44
            nrec = u32(body_off + 40)
        for _ in range(nrec):
            rfmt, rlen = u32(rec_off), u32(rec_off + 4)
            r = rec_off + 8
            rec_off = r + rlen
            if rfmt != 1:  # only raw packet header records
                continue
            proto, frame_len, _stripped, hlen = struct.unpack_from("!IIII", data, r)
            hdr = data[r + 16: r + 16 + hlen]
            nbytes = frame_len * max(rate, 1)
            n += 1
            if proto == 1:
                _account_ether_header(hdr, nbytes, now)
            elif proto == 11:
                _account_ip_header(hdr, nbytes, now)
    with LOCK:
        STATS["records"] += n


def handle_packet(data, now):
    if len(data) < 4:
        return
    if struct.unpack_from("!I", data, 0)[0] == 5:
        with LOCK:
            STATS["packets"]["sflow5"] = STATS["packets"].get("sflow5", 0) + 1
            STATS["last_packet"] = now
        handle_sflow(data, now)
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
            sport, dport = struct.unpack_from("!HH", data, off + 32)
            proto = data[off + 38]
            account(src, dst, octets, now, None, proto, sport, dport)
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
        for (d, client, asn, service), (nbytes, _) in rows:
            if d != direction:
                continue
            as_name = names.get(asn, "unknown") if asn else "unknown"
            app, category = classify(asn, service)
            lines.append(f'{metric}{{client_ip="{ipaddress.IPv4Address(client)}",asn="{asn}",'
                         f'as_name="{_esc(as_name)}",service="{service}",app="{_esc(app)}",'
                         f'category="{_esc(category)}"}} {nbytes}')
    lines.append("# TYPE flow_collector_packets_total counter")
    for v, c in sorted(stats["packets"].items(), key=lambda kv: str(kv[0])):
        lines.append(f'flow_collector_packets_total{{version="{v}"}} {c}')
    for name, help_ in (("records", "Flow records decoded"),
                        ("records_lan_wan", "Flow records between the LAN and the internet"),
                        ("records_post_nat", "Downloads matched to a LAN client by the post-NAT destination"),
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
