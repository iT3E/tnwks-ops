#!/usr/bin/env python3
"""Translate the VyOS `sce-vyos01` configuration into RouterOS Terraform tfvars.

Reads the canonical VyOS source of truth (the `config-parts/*.sh` files from
https://github.com/iT3E/vyos-config) and emits `locals.tf` for the
`modules/mikrotik` Terraform module.

Why this exists rather than hand-written HCL: the VyOS firewall carries 140
rulesets / 195 rule lines / 20 DHCP static mappings / 25 address-group members.
Transcribing that by hand is how you get a subtly wrong firewall. Generating it
means the port is reproducible and re-runnable when the VyOS config changes
before cutover.

Usage:
    ./vyos-to-locals.py --vyos-config ~/src/vyos-config \\
        --out ../../terraform/environments/prod/mikrotik/locals.tf

Non-obvious translations this script performs:

  * VyOS `protocol tcp_udp` has no RouterOS equivalent. Each such rule is
    emitted TWICE, once as tcp and once as udp. This is why the generated rule
    count exceeds the VyOS rule-line count.
  * VyOS accepts IANA service names in port fields (`domain`, `http`, `ntp`).
    RouterOS wants numbers. SERVICE_PORTS below does that mapping.
  * RouterOS caps `dst-port` at 15 entries per rule. Any port list longer than
    that is split across multiple rules (`ad_auth_ports` has 18 entries).
  * VyOS zone `containers` pointed at the Podman pod interface `pod-containers`.
    The container stack is not being ported to the router, so rules referencing
    that zone are emitted as commented-out HCL with a disposition note rather
    than silently dropped.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

# --------------------------------------------------------------------------
# VyOS accepts IANA service names where RouterOS requires port numbers.
# --------------------------------------------------------------------------
SERVICE_PORTS = {
    "domain": "53",
    "domain-s": "853",
    "http": "80",
    "https": "443",
    "ssh": "22",
    "ntp": "123",
    "mdns": "5353",
    "microsoft-ds": "445",
    "bgp": "179",
    "bootps": "67",
    "bootpc": "68",
    "snmp": "161",
    "tftp": "69",
}

# VyOS numeric protocol values that RouterOS names differently.
PROTOCOL_ALIASES = {
    "2": "igmp",
}

# RouterOS hard limit on dst-port list length in a single filter rule.
ROUTEROS_MAX_DST_PORTS = 15

# Zones whose backing interface does not survive the port. VyOS `containers`
# was the Podman pod bridge on the router itself.
UNPORTED_ZONES = {"containers"}

# --------------------------------------------------------------------------
# Disposition of the `containers` zone.
#
# 22 of the 45 VyOS accept rules touch the `containers` zone, so dropping them
# would silently gut half the firewall. They do not all mean the same thing:
#
#   * accept_dns  - client VLAN -> dnsdist at 10.10.53.4. Native RouterOS DNS
#     answers ON the router, so this stops being forwarded traffic entirely and
#     becomes an `input` chain accept. Emitted into `input_rules`.
#   * haproxy / unifi-controller rules - those workloads move to Kubernetes, so
#     the rules keep their meaning but retarget to the k8s zone.
#
# Anything matching DNS_PORT_SPECS becomes an input rule; every other
# containers-zone rule is retargeted via CONTAINER_ZONE_SUCCESSOR.
# --------------------------------------------------------------------------
CONTAINER_ZONE_SUCCESSOR = "k8s-120"

DNS_PORT_SPECS = {"domain,domain-s", "domain", "53", "53,853"}

# Address groups that pointed at container workloads. After the move they are
# reached through the k8s ingress, so the rule keeps its port intent but the
# address list is replaced by the ingress list.
CONTAINER_ADDRESS_GROUPS = {
    "haproxy_frontend": "k8s_ingress",
    "haproxy_authenticated": "k8s_ingress",
    "haproxy_all": "k8s_ingress_internal",
    "unifi_controller": "k8s_ingress_internal",
}

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

VLAN_ID_RE = re.compile(r"^set interfaces ethernet (\S+) vif (\d+) (\S+) '([^']*)'")
WG_RE = re.compile(r"^set interfaces wireguard (\S+) (.+)$")
ZONE_IFACE_RE = re.compile(r"^set firewall zone (\S+) interface '([^']*)'")
ZONE_FROM_RE = re.compile(r"^set firewall zone (\S+) from (\S+) firewall name '([^']*)'")
FW_RULE_RE = re.compile(r"^set firewall ipv4 name (\S+) rule (\d+) (.+)$")
FW_META_RE = re.compile(r"^set firewall ipv4 name (\S+) (default-action|description) '?([^']*)'?$")
ADDR_GROUP_RE = re.compile(r"^set firewall group address-group (\S+) address '([^']*)'")
PORT_GROUP_RE = re.compile(r"^set firewall group port-group (\S+) port '([^']*)'")
DHCP_RE = re.compile(r"^set service dhcp-server shared-network-name (\S+) (.+)$")
NAT_RE = re.compile(r"^set nat destination rule (\d+) (.+)$")
ROUTE_RE = re.compile(r"^set protocols static route (\S+) next-hop (\S+)")

# --------------------------------------------------------------------------
# EdgeRouter Lite consolidation (docs/edgerouter-discovery.md).
#
# The RB5009 replaces THOMAS-ER01 too, so the transit hop disappears. Verified
# on the live ERL on 2026-09-19: 172.16.1.252 and .254 are both `ip neigh`
# FAILED and 100% ping loss, yet both routers still route production subnets at
# them. Anything pointing at a dead next-hop is a bug, not a config to port.
# --------------------------------------------------------------------------
DEAD_NEXT_HOPS = {"172.16.1.252", "172.16.1.254"}

# The old upstream router. With one box there is nothing left to forward to; the
# default route now comes from the WAN DHCP lease.
RETIRED_UPSTREAM = "172.16.1.1"

# Source NAT moves off the ERL onto the RB5009's WAN port. VyOS had its
# masquerade rule commented out precisely because the ERL was doing it.
WAN_INTERFACE = "ether1"

# DDNS, from the EdgeRouter's `service dns dynamic interface eth0 service
# namecheap`, NOT from VyOS (VyOS ran a Cloudflare container for a different
# domain). Kept on Namecheap because the WireGuard client configs use this exact
# hostname as their endpoint: changing providers means reissuing every client
# config, so the record stays put and the migration keeps one less moving part.
DDNS = {
    "provider": "namecheap",
    "host": "ddns",
    "domain": "thomasnetworks.us",
    "interval": "5m",
}
SYSTEM_RE = re.compile(r"^set system (\S+(?: \S+)*) '?([^']*)'?$")


def strip_comments(path: Path) -> list[str]:
    """Return non-comment, non-blank `set ...` lines from a VyOS config-part."""
    lines = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("delete "):
            continue
        lines.append(line)
    return lines


def unquote(value: str) -> str:
    return value.strip().strip("'").strip('"')


def resolve_ports(spec: str) -> list[str]:
    """Expand a VyOS port spec into a list of RouterOS-safe port tokens."""
    out = []
    for token in unquote(spec).split(","):
        token = token.strip()
        if not token:
            continue
        out.append(SERVICE_PORTS.get(token, token))
    return out


def chunk_ports(ports: list[str]) -> list[list[str]]:
    """Split a port list into RouterOS-legal chunks (max 15 entries per rule)."""
    return [
        ports[i : i + ROUTEROS_MAX_DST_PORTS]
        for i in range(0, len(ports), ROUTEROS_MAX_DST_PORTS)
    ] or [[]]


def expand_protocol(proto: str | None) -> list[str | None]:
    """VyOS tcp_udp becomes two RouterOS rules."""
    if proto is None:
        return [None]
    proto = PROTOCOL_ALIASES.get(proto, proto)
    if proto == "tcp_udp":
        return ["tcp", "udp"]
    return [proto]


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def parse_interfaces(lines: list[str]) -> tuple[dict, dict]:
    """Return (vlans_by_description, wireguard_by_name)."""
    vifs: dict[str, dict] = {}
    for line in lines:
        m = VLAN_ID_RE.match(line)
        if not m:
            continue
        _parent, vif, key, value = m.groups()
        entry = vifs.setdefault(vif, {"vlan_id": int(vif)})
        if key == "address":
            entry["address"] = value
        elif key == "description":
            entry["description"] = value

    # Key VLANs by their VyOS description, which is also the zone name. That is
    # what lets firewall rules and VLANs share a vocabulary.
    vlans = {}
    for vif, entry in vifs.items():
        name = entry.get("description") or f"vlan{vif}"
        vlans[name] = {
            "vlan_id": entry["vlan_id"],
            "address": entry.get("address", ""),
            "comment": name,
        }

    wireguard: dict[str, dict] = {}
    for line in lines:
        m = WG_RE.match(line)
        if not m:
            continue
        name, rest = m.groups()
        iface = wireguard.setdefault(name, {"peers": {}})
        if rest.startswith("address "):
            iface["address"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("description "):
            iface["comment"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("port "):
            iface["listen_port"] = int(unquote(rest.split(" ", 1)[1]))
        elif rest.startswith("peer "):
            parts = rest.split(" ", 2)
            peer_name = parts[1]
            peer = iface["peers"].setdefault(peer_name, {})
            attr, value = parts[2].split(" ", 1)
            value = unquote(value)
            if attr == "allowed-ips":
                peer.setdefault("allowed_address", []).append(value)
            elif attr == "persistent-keepalive":
                peer["persistent_keepalive"] = f"{value}s"
            elif attr == "public-key":
                peer["public_key"] = value
    return vlans, wireguard


def parse_zones(lines: list[str]) -> tuple[dict, dict]:
    """Return (zone -> interface, ruleset_name -> (from_zone, to_zone))."""
    zone_iface = {}
    pairs = {}
    for line in lines:
        m = ZONE_IFACE_RE.match(line)
        if m:
            zone_iface[m.group(1)] = m.group(2)
            continue
        m = ZONE_FROM_RE.match(line)
        if m:
            to_zone, from_zone, ruleset = m.groups()
            pairs[ruleset] = (from_zone, to_zone)
    return zone_iface, pairs


def parse_groups(lines: list[str]) -> tuple[dict, dict]:
    address_lists: dict[str, "OrderedDict[str, str]"] = {}
    port_lists: dict[str, list[str]] = {}
    for line in lines:
        m = ADDR_GROUP_RE.match(line)
        if m:
            name, addr = m.groups()
            members = address_lists.setdefault(name, OrderedDict())
            # Member key must be stable so Terraform addresses do not shuffle.
            key = addr.replace(".", "_").replace("/", "_").replace("-", "_to_")
            members[key] = addr
            continue
        m = PORT_GROUP_RE.match(line)
        if m:
            name, port = m.groups()
            port_lists.setdefault(name, []).append(SERVICE_PORTS.get(port, port))
    return address_lists, port_lists


def parse_firewall_rules(lines: list[str]) -> dict:
    """Return ruleset -> {rule_number -> attrs}."""
    rulesets: dict[str, dict] = {}
    for line in lines:
        m = FW_META_RE.match(line)
        if m:
            name, key, value = m.groups()
            rs = rulesets.setdefault(name, {"rules": {}, "meta": {}})
            rs["meta"][key] = value
            continue
        m = FW_RULE_RE.match(line)
        if not m:
            continue
        name, num, rest = m.groups()
        rs = rulesets.setdefault(name, {"rules": {}, "meta": {}})
        rule = rs["rules"].setdefault(int(num), {})

        if rest.startswith("action "):
            rule["action"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("description "):
            rule["description"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("protocol "):
            rule["protocol"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("source group address-group "):
            rule["src_address_list"] = unquote(rest.rsplit(" ", 1)[1])
        elif rest.startswith("destination group address-group "):
            rule["dst_address_list"] = unquote(rest.rsplit(" ", 1)[1])
        elif rest.startswith("destination group port-group "):
            rule["dst_port_list"] = unquote(rest.rsplit(" ", 1)[1])
        elif rest.startswith("destination port "):
            rule["dst_port"] = unquote(rest.split("destination port ", 1)[1])
        elif rest.startswith("source address "):
            rule["src_address"] = unquote(rest.split("source address ", 1)[1])
        elif rest.startswith("destination address "):
            rule["dst_address"] = unquote(rest.split("destination address ", 1)[1])
    return rulesets


def parse_dhcp(lines: list[str]) -> dict:
    nets: dict[str, dict] = {}
    for line in lines:
        m = DHCP_RE.match(line)
        if not m:
            continue
        name, rest = m.groups()
        net = nets.setdefault(name, {"leases": {}})
        if rest == "authoritative":
            net["authoritative"] = "yes"
        elif rest.startswith("subnet "):
            parts = rest.split(" ", 2)
            net["subnet"] = parts[1]
            detail = parts[2]
            if detail.startswith("range 0 start "):
                net["pool_start"] = unquote(detail.rsplit(" ", 1)[1])
            elif detail.startswith("range 0 stop "):
                net["pool_end"] = unquote(detail.rsplit(" ", 1)[1])
            elif detail.startswith("lease "):
                seconds = int(unquote(detail.split(" ", 1)[1]))
                net["lease_time"] = f"{seconds // 3600}h" if seconds % 3600 == 0 else f"{seconds}s"
            elif detail.startswith("name-server "):
                net.setdefault("dns_servers", []).append(unquote(detail.rsplit(" ", 1)[1]))
            elif detail.startswith("default-router "):
                net["gateway"] = unquote(detail.rsplit(" ", 1)[1])
            elif detail.startswith("static-mapping "):
                sm = detail.split(" ", 2)
                host = sm[1]
                attr, value = sm[2].split(" ", 1)
                lease = net["leases"].setdefault(host, {"comment": host})
                if attr == "ip-address":
                    lease["address"] = unquote(value)
                elif attr == "mac-address":
                    lease["mac_address"] = unquote(value).upper()
    return nets


def parse_nat(lines: list[str], zone_iface: dict) -> dict:
    iface_to_zone = {v: k for k, v in zone_iface.items()}
    rules: dict[str, dict] = {}
    for line in lines:
        m = NAT_RE.match(line)
        if not m:
            continue
        num, rest = m.groups()
        rule = rules.setdefault(num, {})
        if rest.startswith("description "):
            rule["comment"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("inbound-interface "):
            vyos_iface = unquote(rest.split(" ", 1)[1])
            rule["_zone"] = iface_to_zone.get(vyos_iface, vyos_iface)
        elif rest.startswith("protocol "):
            rule["protocol"] = unquote(rest.split(" ", 1)[1])
        elif rest.startswith("destination port "):
            rule["dst_port"] = unquote(rest.split("destination port ", 1)[1])
        elif rest.startswith("destination address "):
            rule["dst_address"] = unquote(rest.split("destination address ", 1)[1])
        elif rest.startswith("source address "):
            rule["src_address"] = unquote(rest.split("source address ", 1)[1])
        elif rest.startswith("translation address "):
            rule["to_address"] = unquote(rest.split("translation address ", 1)[1])
        elif rest.startswith("translation port "):
            rule["to_port"] = unquote(rest.split("translation port ", 1)[1])
    return rules


def parse_routes(lines: list[str]) -> dict:
    routes = {}
    for idx, line in enumerate(lines):
        m = ROUTE_RE.match(line)
        if not m:
            continue
        dst, gw = m.groups()
        label = "default" if dst == "0.0.0.0/0" else dst.replace(".", "_").replace("/", "_")
        routes[label] = {"dst_address": dst, "gateway": gw}
    return routes


def parse_system(lines: list[str]) -> dict:
    out: dict[str, object] = {}
    syslog: dict[str, object] = {}
    for line in lines:
        m = SYSTEM_RE.match(line)
        if not m:
            continue
        key, value = m.groups()
        if key == "host-name":
            out["identity"] = value
        elif key == "domain-name":
            out["domain"] = value
        elif key == "time-zone":
            out["timezone"] = value
        elif key.startswith("syslog host "):
            parts = key.split()
            syslog["remote"] = parts[2]
            if "protocol" in key:
                syslog["remote_protocol"] = value
            elif "port" in key:
                syslog["remote_port"] = int(value)
    if syslog:
        out["syslog"] = syslog
    return out


# --------------------------------------------------------------------------
# HCL emitters
# --------------------------------------------------------------------------
def parse_ntp(lines: list[str]) -> dict:
    """VyOS `service ntp` -> RouterOS NTP client/server settings.

    VyOS both consumed upstream NTP and served the LAN (it had allow-client
    ranges), so RouterOS needs server mode on to keep those clients working.
    """
    servers: list[str] = []
    serves_clients = False
    for line in lines:
        m = re.match(r"^set service ntp server (\S+)", line)
        if m:
            servers.append(unquote(m.group(1)))
            continue
        if re.match(r"^set service ntp allow-client", line):
            serves_clients = True
    if not servers:
        return {}
    return {
        "servers": sorted(set(servers)),
        "server_mode": serves_clients,
        "client_mode": "unicast",
    }


def parse_bind_zones(zone_dir: Path) -> tuple[dict, list[str]]:
    """Parse bind zone files into RouterOS static DNS records.

    Only A and CNAME are translated. SOA/NS are bind-internal: RouterOS is a
    forwarding resolver with static overrides, not an authoritative server, so
    it has no equivalent and needs none.
    """
    records: dict[str, dict] = {}
    notes: list[str] = []
    if not zone_dir.is_dir():
        return records, notes

    rr_re = re.compile(
        r"^(?P<name>[@A-Za-z0-9_*.-]+)\s+(?:\d+\s+)?(?:IN\s+)?"
        r"(?P<type>A|CNAME)\s+(?P<value>\S+)\s*$"
    )
    for zone_file in sorted(zone_dir.iterdir()):
        if not zone_file.is_file():
            continue
        origin = None
        for raw in zone_file.read_text().splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            m_origin = re.match(r"^\$ORIGIN\s+(\S+?)\.?$", line)
            if m_origin:
                origin = m_origin.group(1).rstrip(".")
                continue
            m = rr_re.match(line)
            if not m or origin is None:
                continue
            label = m.group("name")
            rtype = m.group("type")
            value = m.group("value")
            fqdn = origin if label == "@" else "%s.%s" % (label, origin)
            key = re.sub(r"[^A-Za-z0-9]+", "_", fqdn).strip("_").lower()
            entry = {
                "name": fqdn,
                "type": rtype,
                "comment": "bind %s" % zone_file.name,
            }
            if rtype == "A":
                entry["address"] = value.rstrip(".")
            else:
                entry["cname"] = value.rstrip(".")
            records[key] = entry
    return records, notes


def parse_dns(vyos_root: Path) -> tuple[dict, list[str]]:
    """Collapse the blocky/dnsdist/bind container stack into native RouterOS DNS.

    Returns the `dns` object plus translation notes for everything that does not
    survive the move off containers.
    """
    notes: list[str] = []
    containers = vyos_root / "containers"
    dns: dict = {
        "upstream_servers": [],
        "allow_remote": True,
        "cache_size": 10240,
        "cache_max_ttl": "1d",
        "static_records": {},
        "adlists": [],
    }

    # --- blocky: upstreams, blocklists, split-horizon overrides ---------------
    blocky_cfg = containers / "blocky" / "config" / "config.yaml"
    if blocky_cfg.is_file():
        cfg = yaml.safe_load(blocky_cfg.read_text()) or {}

        upstreams = (cfg.get("upstream") or {}).get("default") or []
        plain: list[str] = []
        doh = None
        for up in upstreams:
            up = str(up)
            # blocky spells DoT as tcp-tls:HOST:PORT. RouterOS has no DoT, only
            # DoH, so the hosts stay plain resolvers and DoH is layered on top.
            m = re.match(r"^tcp-tls:([0-9.]+)(?::\d+)?$", up)
            if m:
                plain.append(m.group(1))
                continue
            if up.startswith("https://"):
                doh = up
                continue
            plain.append(re.sub(r"^[a-z-]+:", "", up).split(":")[0])
        dns["upstream_servers"] = plain
        if any(p in {"1.1.1.1", "1.0.0.1"} for p in plain):
            doh = doh or "https://cloudflare-dns.com/dns-query"
            notes.append(
                "dns: blocky used DoT (tcp-tls) upstreams; RouterOS has no DoT, "
                "so the same Cloudflare resolvers are configured with DoH "
                "(use_doh_server) plus plain-IP fallback"
            )
        if doh:
            dns["use_doh_server"] = doh
            dns["verify_doh_cert"] = True

        custom = cfg.get("customDNS") or {}
        ttl = custom.get("customTTL", "1h")
        mapping = custom.get("mapping") or {}
        # A wildcard plus its apex collapse into one match_subdomain record.
        wildcards = {k[2:] for k in mapping if k.startswith("*.")}
        for host, addr in mapping.items():
            bare = host[2:] if host.startswith("*.") else host
            if not host.startswith("*.") and host in wildcards:
                continue
            key = re.sub(r"[^A-Za-z0-9]+", "_", bare).strip("_").lower()
            dns["static_records"][key] = {
                "name": bare,
                "address": str(addr).split(",")[0].strip(),
                "type": "A",
                "ttl": ttl,
                "match_subdomain": bare in wildcards,
                "comment": "blocky customDNS",
            }
        if wildcards:
            notes.append(
                "dns: blocky wildcard mappings (*.host) collapsed into single "
                "RouterOS records with match_subdomain=true: %s"
                % sorted(wildcards)
            )
        if custom.get("rewrite"):
            noop = [k for k, v in custom["rewrite"].items() if k == v]
            if noop:
                notes.append(
                    "dns: blocky customDNS.rewrite entries %s are no-ops "
                    "(key == value) and are not ported" % noop
                )

        blocking = cfg.get("blocking") or {}
        adlists: list[str] = []
        for _group, urls in (blocking.get("blackLists") or {}).items():
            adlists.extend(str(u).strip() for u in urls or [])
        dns["adlists"] = adlists
        if adlists:
            notes.append(
                "dns: %d blocky blackList URLs become ip_dns_adlist entries -- "
                "see the RAM warning in docs/mikrotik-vyos-port.md" % len(adlists)
            )
        white = blocking.get("whiteLists") or {}
        white_urls = [u for urls in white.values() for u in (urls or [])]
        if white_urls:
            notes.append(
                "dns: blocky had %d whiteList URL(s); RouterOS ip_dns_adlist has "
                "no allow-list concept, so these are NOT ported (false positives "
                "must be handled with static_records overrides)" % len(white_urls)
            )

    # --- bind: authoritative tnwks.local + unifi zones ------------------------
    zone_records, zone_notes = parse_bind_zones(containers / "bind" / "config" / "zones")
    notes.extend(zone_notes)
    for key, rec in zone_records.items():
        dns["static_records"].setdefault(key, rec)
    if zone_records:
        notes.append(
            "dns: %d bind A/CNAME records ported as RouterOS static entries; "
            "bind SOA/NS have no RouterOS equivalent and are dropped (RouterOS "
            "forwards and overrides, it is not authoritative)" % len(zone_records)
        )

    # --- dnsdist: explicitly not ported ---------------------------------------
    dnsdist_cfg = containers / "dnsdist" / "config" / "dnsdist.conf"
    if dnsdist_cfg.is_file():
        dtext = dnsdist_cfg.read_text()
        pools = sorted(set(re.findall(r'pool\s*=\s*"([^"]+)"', dtext)))
        controld = [p for p in pools if "controld" in p]
        if controld:
            notes.append(
                "dns: dnsdist per-subnet ControlD DoH pools %s are NOT ported; "
                "RouterOS resolves uniformly. Per-client policy needs "
                "ip_dns_forwarders or an off-router resolver" % controld
            )
        if "DropAction" in dtext:
            notes.append(
                "dns: dnsdist DropAction rules are NOT ported (no RouterOS "
                "equivalent in the DNS path; use firewall rules instead)"
            )

    dns["static_records"] = dict(sorted(dns["static_records"].items()))
    return dns, notes


def parse_admin_keys(lines: list[str]) -> tuple[list[str], list[str]]:
    """Extract active SSH public keys from `system login user`.

    VyOS interpolated the username from ${SSH_VYOS_USERNAME}, so the username is
    injected from SOPS in main.tf and only the key material is emitted here.
    Commented-out keys in the source are intentionally ignored.
    """
    keys: list[str] = []
    notes: list[str] = []
    key_re = re.compile(
        r"^set system login user \S+ authentication public-keys (\S+) key '([^']+)'"
    )
    type_re = re.compile(
        r"^set system login user \S+ authentication public-keys (\S+) type '([^']+)'"
    )
    types: dict[str, str] = {}
    found: dict[str, str] = {}
    for line in lines:
        m = key_re.match(line)
        if m:
            found[m.group(1)] = m.group(2)
            continue
        m = type_re.match(line)
        if m:
            types[m.group(1)] = m.group(2)
    for name, material in sorted(found.items()):
        ktype = types.get(name, "ssh-rsa")
        keys.append("%s %s" % (ktype, material))
    if keys:
        notes.append(
            "system: %d SSH public key(s) ported from VyOS `system login user`; "
            "the username came from ${SSH_VYOS_USERNAME} and is injected from "
            "SOPS in main.tf" % len(keys)
        )
    return keys, notes


def hcl(value, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        return '"%s"' % value.replace('"', '\\"')
    if isinstance(value, list):
        if not value:
            return "[]"
        items = ", ".join(hcl(v, indent) for v in value)
        if len(items) <= 90:
            return f"[{items}]"
        inner = "\n".join(f"{pad}  {hcl(v, indent + 1)}," for v in value)
        return "[\n%s\n%s]" % (inner, pad)
    if isinstance(value, dict):
        if not value:
            return "{}"
        # HCL bare map keys must be valid identifiers: leading letter/underscore.
        # Generated keys are deliberately numeric-prefixed (NNN-from-to) to make
        # sorted order equal firewall order, so most of them need quoting.
        keys = {k: (k if IDENT_RE.match(k) else '"%s"' % k) for k in value}
        width = max(len(v) for v in keys.values())
        lines = [
            "%s  %-*s = %s" % (pad, width, keys[k], hcl(v, indent + 1))
            for k, v in value.items()
        ]
        return "{\n%s\n%s}" % ("\n".join(lines), pad)
    raise TypeError(type(value))


def build_zone_policies(rulesets: dict, pairs: dict, zone_iface: dict) -> tuple[dict, dict, list[str]]:
    """Turn VyOS rulesets into RouterOS zone-pair policies plus input rules.

    Returns (zone_policies, input_rules, notes).
    """
    policies: "OrderedDict[str, dict]" = OrderedDict()
    input_rules: "OrderedDict[str, dict]" = OrderedDict()
    notes: list[str] = []
    seq = 0
    input_seq = 0

    def emit_variants(rule: dict, num: int) -> list[dict]:
        """Expand one VyOS rule into RouterOS-legal variants."""
        base = {"comment": rule.get("description") or f"rule {num}"}
        for key in ("src_address_list", "dst_address_list", "src_address", "dst_address"):
            if key in rule:
                base[key] = rule[key]

        if "dst_port_list" in rule:
            base["dst_port_list"] = rule["dst_port_list"]
            port_chunks = [None]
        elif "dst_port" in rule:
            port_chunks = chunk_ports(resolve_ports(rule["dst_port"]))
        else:
            port_chunks = [None]

        out = []
        for proto in expand_protocol(rule.get("protocol")):
            for chunk_idx, chunk in enumerate(port_chunks):
                emitted = dict(base)
                if proto:
                    emitted["protocol"] = proto
                if chunk:
                    emitted["dst_port"] = ",".join(chunk)
                if len(port_chunks) > 1:
                    emitted["comment"] += f" [ports {chunk_idx + 1}/{len(port_chunks)}]"
                out.append(emitted)
        return out

    for ruleset in sorted(rulesets):
        data = rulesets[ruleset]
        if not data["rules"]:
            # 110 of the 140 VyOS rulesets are empty: the pair exists but has no
            # accepts, so it is pure default-drop. The absence of any accept plus
            # the terminal forward drop already expresses that; no chain needed.
            continue

        from_zone, to_zone = pairs.get(ruleset, (None, None))
        if from_zone is None:
            notes.append(
                f"{ruleset}: orphaned ruleset, no `firewall zone ... from` binding "
                f"in the VyOS config, so it never applied (pre-existing VyOS drift)"
            )
            continue

        # --- containers zone disposition -----------------------------------
        if to_zone in UNPORTED_ZONES:
            for num in sorted(data["rules"]):
                rule = data["rules"][num]
                if rule.get("action") != "accept":
                    continue
                if rule.get("dst_port", "") in DNS_PORT_SPECS:
                    # The resolver now runs ON the router, so this stops being
                    # forwarded traffic and becomes an input-chain accept.
                    for variant in emit_variants(rule, num):
                        input_seq += 10
                        entry = {
                            "comment": f"DNS from {from_zone} (was {ruleset})",
                            # Scoped to the originating zone, so it can never be
                            # satisfied from the WAN. The EdgeRouter's 0.0.0.0
                            # DNS bind is exactly what this avoids.
                            "in_interface_list": from_zone,
                        }
                        for key in ("protocol", "dst_port"):
                            if key in variant:
                                entry[key] = variant[key]
                        input_rules[f"{input_seq:03d}-dns-{from_zone}-{entry.get('protocol', 'any')}"] = entry
                    notes.append(
                        f"{ruleset} rule {num} (accept_dns): moved to the input chain; "
                        f"native RouterOS DNS replaces the dnsdist container"
                    )
                elif from_zone == CONTAINER_ZONE_SUCCESSOR:
                    # Both ends land in the same VLAN after the move. Intra-VLAN
                    # traffic is switched locally and never reaches the router's
                    # forward chain, so a rule here would be dead config. This
                    # becomes a Kubernetes NetworkPolicy concern instead.
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED as a router rule; after the "
                        f"container->k8s move both ends are inside {CONTAINER_ZONE_SUCCESSOR}, "
                        f"so it is intra-VLAN traffic the router never sees. Enforce with a "
                        f"Kubernetes NetworkPolicy if it still matters."
                    )
                else:
                    # haproxy / unifi-controller workloads move to Kubernetes.
                    retarget = dict(rule)
                    grp = retarget.get("dst_address_list")
                    if grp in CONTAINER_ADDRESS_GROUPS:
                        retarget["dst_address_list"] = CONTAINER_ADDRESS_GROUPS[grp]
                    key = f"{(seq + 10):03d}-{from_zone}-{CONTAINER_ZONE_SUCCESSOR}"
                    if key not in policies:
                        seq += 10
                        key = f"{seq:03d}-{from_zone}-{CONTAINER_ZONE_SUCCESSOR}"
                        policies[key] = {
                            "from": from_zone,
                            "to": CONTAINER_ZONE_SUCCESSOR,
                            "comment": f"{from_zone} -> {CONTAINER_ZONE_SUCCESSOR} (ported from {ruleset})",
                            "rules": [],
                        }
                    for variant in emit_variants(retarget, num):
                        variant["comment"] += " (container -> k8s)"
                        policies[key]["rules"].append(variant)
                    notes.append(
                        f"{ruleset} rule {num}: retargeted {from_zone} -> "
                        f"{CONTAINER_ZONE_SUCCESSOR}; workload moves to Kubernetes"
                    )
            continue

        if from_zone in UNPORTED_ZONES:
            # containers -> X. The source is now a k8s pod, so the traffic
            # arrives from the k8s VLAN instead of the router's pod bridge.
            for num in sorted(data["rules"]):
                rule = data["rules"][num]
                if rule.get("action") != "accept":
                    continue
                if to_zone == CONTAINER_ZONE_SUCCESSOR:
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED as a router rule; after the "
                        f"container->k8s move both ends are inside {CONTAINER_ZONE_SUCCESSOR}, "
                        f"so it is intra-VLAN traffic the router never sees. Enforce with a "
                        f"Kubernetes NetworkPolicy if it still matters."
                    )
                    continue
                key = f"{(seq + 10):03d}-{CONTAINER_ZONE_SUCCESSOR}-{to_zone}"
                if key not in policies:
                    seq += 10
                    key = f"{seq:03d}-{CONTAINER_ZONE_SUCCESSOR}-{to_zone}"
                    policies[key] = {
                        "from": CONTAINER_ZONE_SUCCESSOR,
                        "to": to_zone,
                        "comment": f"{CONTAINER_ZONE_SUCCESSOR} -> {to_zone} (ported from {ruleset})",
                        "rules": [],
                    }
                for variant in emit_variants(rule, num):
                    variant["comment"] += " (container -> k8s)"
                    policies[key]["rules"].append(variant)
                notes.append(
                    f"{ruleset} rule {num}: source retargeted to "
                    f"{CONTAINER_ZONE_SUCCESSOR}; workload moves to Kubernetes"
                )
            continue

        # --- normal zone pair ----------------------------------------------
        rules_out = []
        for num in sorted(data["rules"]):
            rule = data["rules"][num]
            if rule.get("action") != "accept":
                notes.append(f"{ruleset} rule {num}: action={rule.get('action')}, not an accept")
                continue
            rules_out.extend(emit_variants(rule, num))

        if not rules_out:
            continue

        seq += 10
        policies[f"{seq:03d}-{from_zone}-{to_zone}"] = {
            "from": from_zone,
            "to": to_zone,
            "comment": data["meta"].get("description", ruleset),
            "rules": rules_out,
        }

    # Router-destined services. VyOS left input unfiltered (no local zone), so
    # these are additions rather than ports. They are required for the router to
    # stay reachable and functional once the input chain ends in a drop.
    input_seq = max(input_seq, 500)
    for offset, extra in enumerate([
        {"comment": "ICMP for path MTU discovery and diagnostics", "protocol": "icmp"},
        # LAN-only. The EdgeRouter Lite bound DHCP/NTP/DNS to 0.0.0.0, which left
        # it a public open resolver and open NTP reflector on the WAN address.
        # in_interface_list = "lan" is what keeps that from coming back.
        {"comment": "DHCP requests from LAN clients", "protocol": "udp",
         "dst_port": "67,68", "in_interface_list": "lan"},
        {"comment": "NTP server mode for LAN clients only (never WAN)", "protocol": "udp",
         "dst_port": "123", "in_interface_list": "lan"},
        {"comment": "SSH from the mgmt VLAN only", "protocol": "tcp",
         "dst_port": "22", "in_interface_list": "unifi-mgmt-900"},
        {"comment": "RouterOS REST API and Winbox from the mgmt VLAN only", "protocol": "tcp",
         "dst_port": "443,8291", "in_interface_list": "unifi-mgmt-900"},
        # The one service that must answer on the WAN. Replaces EdgeRouter
        # port-forward rules 3 and 4, which forwarded 51820/51821 to VyOS at
        # 172.16.1.250; this router terminates the tunnels itself.
        {"comment": "WireGuard from the internet (replaces ERL port-forward 3/4)",
         "protocol": "udp", "dst_port": "51820,51821", "in_interface": WAN_INTERFACE},
    ]):
        input_rules[f"{input_seq + (offset + 1) * 10:03d}-router-service"] = extra

    return policies, input_rules, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vyos-config", required=True, type=Path,
                    help="Path to a clone of iT3E/vyos-config")
    ap.add_argument("--out", required=True, type=Path,
                    help="locals.tf destination in the prod/mikrotik root module")
    args = ap.parse_args()

    parts = args.vyos_config / "config-parts"
    if not parts.is_dir():
        print("error: %s not found" % parts, file=sys.stderr)
        return 1

    read = lambda name: strip_comments(parts / name)

    vlans, wireguard = parse_interfaces(read("interfaces.sh"))
    zone_iface, pairs = parse_zones(read("firewall-zone.sh"))
    address_lists, port_lists = parse_groups(read("firewall.sh"))
    rulesets = parse_firewall_rules(read("firewall-name.sh"))
    dhcp = parse_dhcp(read("service-dhcp_server.sh"))
    nat = parse_nat(read("nat.sh"), zone_iface)
    routes = parse_routes(read("protocols.sh"))
    system = parse_system(read("system.sh"))
    ntp = parse_ntp(read("service.sh"))
    admin_keys, admin_notes = parse_admin_keys(read("system.sh"))
    dns, dns_notes = parse_dns(args.vyos_config)

    # Attach DHCP config onto the matching VLAN entry.
    for zone, net in dhcp.items():
        if zone not in vlans:
            continue
        if "pool_start" not in net or "pool_end" not in net:
            continue
        vlans[zone]["dhcp"] = {
            "pool_start": net["pool_start"],
            "pool_end": net["pool_end"],
            "lease_time": net.get("lease_time", "24h"),
            "authoritative": net.get("authoritative", "yes"),
            "leases": {k: v for k, v in sorted(net["leases"].items())},
        }

    # VLANs with no live devices stay in the file, disabled. Anything that both
    # lacks DHCP and lacks firewall rules is presumed dormant.
    active_zones = set()
    for ruleset, (frm, to) in pairs.items():
        if rulesets.get(ruleset, {}).get("rules"):
            active_zones.update({frm, to})
    for name, vlan in vlans.items():
        if "dhcp" not in vlan and name not in active_zones:
            vlan["enabled"] = False

    zone_policies, input_rules, notes = build_zone_policies(rulesets, pairs, zone_iface)
    notes.extend(dns_notes)
    notes.extend(admin_notes)

    # --- EdgeRouter consolidation: prune routes that no longer make sense ----
    kept_routes = {}
    for label, route in routes.items():
        gw = route["gateway"]
        dst = route["dst_address"]
        if gw in DEAD_NEXT_HOPS:
            notes.append(
                "route %s via %s DROPPED: next-hop is dead (ip neigh FAILED, 100%% "
                "ping loss on 2026-09-19). Both VyOS and the EdgeRouter still "
                "pointed production subnets at it" % (dst, gw)
            )
            continue
        if dst == "0.0.0.0/0" and gw == RETIRED_UPSTREAM:
            notes.append(
                "route 0.0.0.0/0 via %s DROPPED: that was the EdgeRouter Lite, "
                "which this router replaces. The default route now comes from the "
                "WAN DHCP lease (add_default_route = yes)" % gw
            )
            continue
        kept_routes[label] = route
    routes = kept_routes

    # NAT: rewrite VyOS interface references to RouterOS VLAN interface names.
    dstnat = {}
    for num, rule in sorted(nat.items()):
        zone = rule.pop("_zone", None)
        if zone not in vlans:
            notes.append("nat rule %s: zone %s not a ported VLAN" % (num, zone))
            continue
        vlan_id = vlans[zone]["vlan_id"]
        entry = {
            "comment": rule.get("comment", "nat %s" % num),
            "in_interface": "bridge-lan-vlan%s" % vlan_id,
            "protocol": rule.get("protocol", "udp"),
            "dst_port": rule.get("dst_port", ""),
            "to_address": rule.get("to_address", ""),
            "to_port": rule.get("to_port", ""),
        }
        for key in ("dst_address", "src_address"):
            if key in rule:
                entry[key] = rule[key]

        # A redirect to a dead host is a black hole, not a feature. The VyOS
        # transit-10 NTP rule pointed at 172.16.1.254, which means NTP for that
        # zone has been silently failing; do not carry the bug forward.
        if entry["to_address"] in DEAD_NEXT_HOPS:
            notes.append(
                "nat rule %s (%s) DROPPED: redirect target %s is dead, so this "
                "rule black-holes traffic today. Verified 2026-09-19"
                % (num, entry["comment"], entry["to_address"])
            )
            continue

        for proto in expand_protocol(entry["protocol"]):
            dstnat["%s-%s" % (num, proto)] = dict(entry, protocol=proto)

    # WireGuard: strip private keys, main.tf injects them from SOPS.
    wg_out = {}
    for name, iface in sorted(wireguard.items()):
        wg_out[name] = {
            "address": iface.get("address", ""),
            "listen_port": iface.get("listen_port", 51820),
            "comment": iface.get("comment", name),
            "peers": {k: v for k, v in sorted(iface["peers"].items())},
        }

    # Every enabled VLAN is a LAN interface. The WAN port is physical and is
    # deliberately absent, which is what makes the input-chain scoping safe.
    lan_zones = [n for n, v in vlans.items() if v.get("enabled") is not False]

    # EdgeRouter `system conntrack` values, ported so the consolidated router is
    # sized for the edge load rather than RouterOS defaults.
    # Strings, not bools: RouterOS spells these "yes"/"no"/"auto" and the
    # provider schema types them as String.
    conntrack = {"enabled": "yes", "loose_tcp_tracking": "yes"}

    rule_count = sum(len(p["rules"]) for p in zone_policies.values())
    enabled_vlans = sum(1 for v in vlans.values() if v.get("enabled") is not False)
    dhcp_servers = sum(1 for v in vlans.values() if "dhcp" in v)
    lease_count = sum(len(v["dhcp"]["leases"]) for v in vlans.values() if "dhcp" in v)

    header = """## ------------------------------------------------------------------------------------------------------\
---------------
## GENERATED FILE - DO NOT EDIT BY HAND
##
## Produced by infrastructure/mikrotik/tools/vyos-to-locals.py from the VyOS
## config at github.com/iT3E/vyos-config (config-parts/*.sh + containers/*).
##
## Regenerate with:
##   task mikrotik:generate
##
## Values live here rather than in a .tfvars file to match the rest of this repo
## (environments/prod/aws, environments/prod/cloudflare and bootstrap/aws-init
## all inline their values and pull secrets from SOPS). Secrets are NOT here:
## see secrets.sops.yaml.
##
## Translation summary:
##   VLANs                %d (%d enabled)
##   DHCP servers         %d
##   DHCP static leases   %d
##   Address lists        %d (%d members)
##   Port lists           %d
##   Zone-pair chains     %d
##   Firewall accepts     %d (expanded from VyOS tcp_udp / port-limit splits)
##   Input-chain accepts  %d
##   dstnat rules         %d
##   Static routes        %d
##   WireGuard listeners  %d
##   DNS static records   %d
##   DNS adlists          %d
## ------------------------------------------------------------------------------------------------------\
---------------

""" % (
        len(vlans), enabled_vlans, dhcp_servers, lease_count,
        len(address_lists), sum(len(m) for m in address_lists.values()),
        len(port_lists), len(zone_policies), rule_count, len(input_rules),
        len(dstnat), len(routes), len(wg_out),
        len(dns.get("static_records", {})), len(dns.get("adlists", [])),
    )
    header = header.replace("\\\n", "")

    def assign(name: str, value, comment: str | None = None) -> str:
        out = ""
        if comment:
            for line in comment.strip().split("\n"):
                out += "  # %s\n" % line
        return out + "  %s = %s\n" % (name, hcl(value, 1))

    # The VyOS box is sce-vyos01. This is a NEW device that will run alongside it
    # until the trunk swings, so it gets its own name, matching the bootstrap
    # script. Emitted rather than inherited so the two never collide on the wire.
    vyos_identity = system.get("identity", "sce-vyos01")
    identity = "sce-rtr01"
    if vyos_identity != identity:
        notes.append(
            "system: identity is %s, NOT the VyOS name %s -- the two run "
            "concurrently until the trunk swings. The bind zone has an A record "
            "for %s (172.16.1.250); add one for %s before cutover"
            % (identity, vyos_identity, vyos_identity, identity)
        )

    body = ["locals {\n"]
    body.append(assign("identity", identity))
    body.append(assign("domain", system.get("domain", "tnwks.local")))
    body.append(assign("timezone", system.get("timezone", "America/Los_Angeles")))
    body.append("\n")
    body.append(assign("wan_interface", "ether1",
                       "ether1 is WAN; the rest of the ports form the VLAN trunk bridge."))
    body.append(assign("lan_trunk_interface", "bridge-lan"))
    body.append(assign("bridge_name", "bridge-lan"))
    body.append(assign("bridge_ports", [
        "ether2", "ether3", "ether4", "ether5",
        "ether6", "ether7", "ether8", "sfp-sfpplus1",
    ]))
    body.append("\n")
    body.append(assign("vlans", dict(sorted(vlans.items()))))
    body.append("\n")
    body.append(assign("static_routes", dict(sorted(routes.items()))))
    body.append("\n")
    body.append(assign("address_lists",
                       {k: dict(v) for k, v in sorted(address_lists.items())}))
    body.append("\n")
    body.append(assign("port_lists", {k: v for k, v in sorted(port_lists.items())},
                       "RouterOS has no port-group object; these render into dst_port strings."))
    body.append("\n")
    body.append(assign("zone_policies", dict(zone_policies),
                       "Order is semantic. Keys are NNN-from-to; routeros_move_items\n"
                       "re-sequences the forward chain to sorted key order after apply."))
    body.append("\n")
    body.append(assign("input_rules", dict(input_rules),
                       "Input chain. No VyOS counterpart: VyOS had no local zone so traffic\n"
                       "TO the router was unfiltered. Intentional hardening delta, and where\n"
                       "the ported client-DNS rules land now the resolver is the router."))
    body.append("\n")
    body.append(assign("dstnat_rules", dict(sorted(dstnat.items()))))
    body.append("\n")
    body.append(assign("masquerade_out_interface", WAN_INTERFACE,
                       "Source NAT moves here from the EdgeRouter Lite (its\n"
                       "`service nat rule 5000 type masquerade outbound-interface eth0`).\n"
                       "VyOS had its own masquerade commented out because the ERL did it."))
    body.append("\n")
    body.append(assign("dns", dns,
                       "Replaces the blocky + dnsdist + bind container stack with native\n"
                       "RouterOS DNS. See docs/mikrotik-vyos-port.md for what did not survive."))
    body.append("\n")
    body.append(assign("ntp", ntp,
                       "VyOS both consumed upstream NTP and served the LAN (allow-client),\n"
                       "so server_mode stays on."))
    if system.get("syslog"):
        body.append("\n")
        body.append(assign("syslog", system["syslog"],
                           "VyOS shipped octet-counted TCP syslog to the k8s Vector aggregator.\n"
                           "RouterOS emits plain syslog, so the Vector source must accept it."))
    body.append("\n")
    body.append(assign("admin_ssh_keys", admin_keys,
                       "Username is injected from SOPS in main.tf (VyOS used\n"
                       "${SSH_VYOS_USERNAME}), so only key material lives here."))
    body.append("\n")
    body.append("\n")
    body.append(assign("lan_interface_lists", sorted(lan_zones),
                       "Members of the aggregate zone-lan interface list. Input-chain\n"
                       "rules for DNS/NTP/DHCP match this so they cannot be reached from\n"
                       "the WAN, closing the EdgeRouter's open-resolver/open-NTP exposure."))
    body.append("\n")
    body.append(assign("connection_tracking", conntrack,
                       "Carried over from the EdgeRouter Lite, which sized conntrack for\n"
                       "the full internet-edge load (table-size 32768, tcp loose enable)."))
    body.append("\n")
    body.append(assign("ddns", DDNS,
                       "From the EdgeRouter's `service dns dynamic` (namecheap), not VyOS.\n"
                       "RouterOS has no Namecheap client, so the module renders a script +\n"
                       "scheduler onto the router; see modules/mikrotik/ddns.tf.\n"
                       "LOAD-BEARING: the WireGuard client configs use this hostname as\n"
                       "their endpoint, so a stale record breaks remote VPN access at the\n"
                       "next WAN address change. The password comes from SOPS."))
    body.append("\n")
    body.append(assign("wireguard_interfaces", wg_out,
                       "Private keys are injected from SOPS in main.tf, never stored here."))

    if notes:
        body.append("\n  # Translation notes (see docs/mikrotik-vyos-port.md):\n")
        for note in sorted(set(notes)):
            body.append("  #   - %s\n" % note)
    body.append("}\n")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(header + "".join(body))

    print("wrote %s" % args.out)
    print("  vlans=%d zone_chains=%d accepts=%d dstnat=%d leases=%d"
          % (len(vlans), len(zone_policies), rule_count, len(dstnat), lease_count))
    print("  input_rules=%d dns_records=%d adlists=%d ssh_keys=%d"
          % (len(input_rules), len(dns.get("static_records", {})),
             len(dns.get("adlists", [])), len(admin_keys)))
    if notes:
        print("  %d translation notes (recorded in the locals footer)" % len(set(notes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
