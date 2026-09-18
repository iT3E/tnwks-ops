#!/usr/bin/env python3
"""Translate the VyOS `sce-vyos01` configuration into RouterOS Terraform tfvars.

Reads the canonical VyOS source of truth (the `config-parts/*.sh` files from
https://github.com/iT3E/vyos-config) and emits `terraform.tfvars` for the
`modules/mikrotik` Terraform module.

Why this exists rather than hand-written HCL: the VyOS firewall carries 140
rulesets / 195 rule lines / 20 DHCP static mappings / 25 address-group members.
Transcribing that by hand is how you get a subtly wrong firewall. Generating it
means the port is reproducible and re-runnable when the VyOS config changes
before cutover.

Usage:
    ./vyos-to-tfvars.py --vyos-config ~/src/vyos-config \\
        --out ../../terraform/environments/prod/mikrotik/terraform.tfvars

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
        {"comment": "DHCP requests from LAN clients", "protocol": "udp", "dst_port": "67,68"},
        {"comment": "NTP server mode for LAN clients", "protocol": "udp", "dst_port": "123"},
        {"comment": "SSH from the mgmt VLAN only", "protocol": "tcp",
         "dst_port": "22", "in_interface_list": "unifi-mgmt-900"},
        {"comment": "RouterOS REST API and Winbox from the mgmt VLAN only", "protocol": "tcp",
         "dst_port": "443,8291", "in_interface_list": "unifi-mgmt-900"},
        {"comment": "WireGuard listeners", "protocol": "udp", "dst_port": "51820,51821"},
    ]):
        input_rules[f"{input_seq + (offset + 1) * 10:03d}-router-service"] = extra

    return policies, input_rules, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vyos-config", required=True, type=Path,
                    help="Path to a clone of iT3E/vyos-config")
    ap.add_argument("--out", required=True, type=Path,
                    help="terraform.tfvars destination")
    args = ap.parse_args()

    parts = args.vyos_config / "config-parts"
    if not parts.is_dir():
        print(f"error: {parts} not found", file=sys.stderr)
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

    # NAT: rewrite VyOS interface references to RouterOS VLAN interface names.
    dstnat = {}
    for num, rule in sorted(nat.items()):
        zone = rule.pop("_zone", None)
        if zone not in vlans:
            notes.append(f"nat rule {num}: zone {zone} not a ported VLAN")
            continue
        vlan_id = vlans[zone]["vlan_id"]
        entry = {
            "comment": rule.get("comment", f"nat {num}"),
            "in_interface": f"bridge-lan-vlan{vlan_id}",
            "protocol": rule.get("protocol", "udp"),
            "dst_port": rule.get("dst_port", ""),
            "to_address": rule.get("to_address", ""),
            "to_port": rule.get("to_port", ""),
        }
        for key in ("dst_address", "src_address"):
            if key in rule:
                entry[key] = rule[key]
        for proto in expand_protocol(entry["protocol"]):
            suffixed = dict(entry, protocol=proto)
            dstnat[f"{num}-{proto}"] = suffixed

    # WireGuard: strip private keys, main.tf injects them from SOPS.
    wg_out = {}
    for name, iface in sorted(wireguard.items()):
        wg_out[name] = {
            "address": iface.get("address", ""),
            "listen_port": iface.get("listen_port", 51820),
            "comment": iface.get("comment", name),
            "peers": {k: v for k, v in sorted(iface["peers"].items())},
        }

    rule_count = sum(len(p["rules"]) for p in zone_policies.values())
    header = f"""## ---------------------------------------------------------------------------------------------------------------------
## GENERATED FILE - DO NOT EDIT BY HAND
##
## Produced by infrastructure/mikrotik/tools/vyos-to-tfvars.py from the VyOS
## config at github.com/iT3E/vyos-config (config-parts/*.sh).
##
## Regenerate with:
##   task mikrotik:tfvars
##
## Translation summary:
##   VLANs                {len(vlans)} ({sum(1 for v in vlans.values() if v.get('enabled') is not False)} enabled)
##   DHCP servers         {sum(1 for v in vlans.values() if 'dhcp' in v)}
##   DHCP static leases   {sum(len(v['dhcp']['leases']) for v in vlans.values() if 'dhcp' in v)}
##   Address lists        {len(address_lists)} ({sum(len(m) for m in address_lists.values())} members)
##   Port lists           {len(port_lists)}
##   Zone-pair chains     {len(zone_policies)}
##   Firewall accepts     {rule_count} (expanded from VyOS tcp_udp / port-limit splits)
##   dstnat rules         {len(dstnat)}
##   Static routes        {len(routes)}
##   WireGuard listeners  {len(wg_out)}
## ---------------------------------------------------------------------------------------------------------------------

"""

    body = []
    body.append(f'identity = {hcl(system.get("identity", "sce-rtr01"))}\n')
    body.append(f'domain   = {hcl(system.get("domain", "tnwks.local"))}\n')
    body.append(f'timezone = {hcl(system.get("timezone", "America/Los_Angeles"))}\n')
    body.append("\n# ether1 is WAN; the rest of the ports form the VLAN trunk bridge.\n")
    body.append('wan_interface       = "ether1"\n')
    body.append('lan_trunk_interface = "bridge-lan"\n')
    body.append('bridge_name         = "bridge-lan"\n')
    body.append('bridge_ports        = %s\n' % hcl(
        ["ether2", "ether3", "ether4", "ether5", "ether6", "ether7", "ether8", "sfp-sfpplus1"]))
    body.append("\nvlans = %s\n" % hcl(dict(sorted(vlans.items()))))
    body.append("\nstatic_routes = %s\n" % hcl(dict(sorted(routes.items()))))
    body.append("\naddress_lists = %s\n" % hcl(
        {k: dict(v) for k, v in sorted(address_lists.items())}))
    body.append("\n# RouterOS has no port-group object; these render into dst_port strings.\n")
    body.append("port_lists = %s\n" % hcl({k: v for k, v in sorted(port_lists.items())}))
    body.append("\n# Order is semantic. Keys are NNN-from-to; routeros_move_items re-sequences\n")
    body.append("# the forward chain to sorted key order after apply.\n")
    body.append("zone_policies = %s\n" % hcl(dict(zone_policies)))
    body.append("\n# Input chain. No VyOS counterpart: VyOS had no local zone so traffic TO the\n")
    body.append("# router was unfiltered. This is an intentional hardening delta, and it is\n")
    body.append("# where the ported client-DNS rules land now the resolver is the router.\n")
    body.append("input_rules = %s\n" % hcl(dict(input_rules)))
    body.append("\ndstnat_rules = %s\n" % hcl(dict(sorted(dstnat.items()))))
    body.append("\n# VyOS had source NAT commented out; the EdgeRouter does the NAT.\n")
    body.append("masquerade_out_interface = null\n")
    body.append("\nwireguard_interfaces = %s\n" % hcl(wg_out))

    if notes:
        body.append("\n## Translation notes (see docs/mikrotik-vyos-port.md):\n")
        for note in sorted(set(notes)):
            body.append(f"##   - {note}\n")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(header + "".join(body))

    print(f"wrote {args.out}")
    print(f"  vlans={len(vlans)} zone_chains={len(zone_policies)} accepts={rule_count} "
          f"dstnat={len(dstnat)} leases={sum(len(v['dhcp']['leases']) for v in vlans.values() if 'dhcp' in v)}")
    print(f"  input_rules={len(input_rules)}")
    if notes:
        print(f"  {len(set(notes))} translation notes (recorded in the tfvars footer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
