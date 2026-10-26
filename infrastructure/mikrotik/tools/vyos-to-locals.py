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
    that is split across multiple rules.
  * VyOS zone `containers` pointed at the Podman pod interface `pod-containers`.
    The container stack is not being ported to the router, so every rule that
    references that zone is either moved (DNS -> input chain), retargeted to
    the workload's new home, or dropped with a note in the locals.tf footer.
  * VyOS `default-action` is honoured per ruleset. Default-drop pairs with no
    rules emit nothing (the module's terminal forward drop covers them);
    default-accept pairs are emitted with `default_action = "accept"`, because
    they are what let every VLAN reach the internet.
"""

from __future__ import annotations

import argparse
import ipaddress
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
#
# Where the surviving container workloads live now. Kubernetes runs on the WSL
# desktop (homelab-wsl), which the LAN reaches through the Windows host on VLAN
# 910; its ingress and any relayed services answer on that one address.
# --------------------------------------------------------------------------
WSL_HOST = "10.10.91.142"
CONTAINER_ZONE_SUCCESSOR = "unifi-frontend-910"

DNS_PORT_SPECS = {"domain,domain-s", "domain", "53", "53,853"}

# The DNS containers. Forced-DNS dstnat that targeted them becomes a redirect
# to the router, which is the resolver now.
CONTAINER_DNS_ADDRESSES = {"10.10.53.4", "10.10.53.7"}

# Where the UniFi Network controller (container 10.10.53.10) moves: Kubernetes
# on the WSL desktop, reached from the LAN through the Windows host. The APs
# need TCP 8080 (inform), UDP 3478 (STUN) and the UI on TCP 8443 relayed there
# (docs/wsl-lan-exposure.md).
UNIFI_CONTROLLER_HOME = WSL_HOST

# Container address groups that still mean something after the move, mapped to
# the address list that replaces them. Lists here are (re)defined in
# NEW_ADDRESS_LISTS.
CONTAINER_ADDRESS_GROUPS = {
    "unifi_controller": "unifi_controller",
}

# haproxy is retired, not migrated: every backend it proxied to (the old
# 10.10.120.x cluster and its ingress) is gone, and the WSL cluster serves those
# apps on *.internal.tnwks.us through its own ingress. Rules that reached
# haproxy are dropped rather than retargeted.
HAPROXY_ONLY_GROUPS = {"haproxy_all", "haproxy_frontend", "haproxy_authenticated"}

# containers -> X rulesets: which container was the SOURCE. Anything not listed
# was haproxy talking to its backends and is dropped with haproxy.
CONTAINER_SOURCE_RULESETS = {
    "containers-unifi-mgmt-900": "unifi_controller",  # controller -> APs (SSH adopt, STUN)
}

# Port groups that implied a container destination without naming one.
CONTAINER_PORT_GROUP_DESTINATIONS = {
    "unifi_controller_ports": "unifi_controller",
}

NEW_ADDRESS_LISTS = {
    "unifi_controller": {UNIFI_CONTROLLER_HOME.replace(".", "_"): UNIFI_CONTROLLER_HOME},
    # Frigate runs on the WSL cluster, so its camera pulls come from the Windows host.
    "frigate": {WSL_HOST.replace(".", "_"): WSL_HOST},
    # mktxp (WSL cluster) is the only client of the router's read-only API.
    "monitoring": {WSL_HOST.replace(".", "_"): WSL_HOST},
}

# Lists the module references directly rather than through a generated rule,
# so the unused-list pruning must keep them.
MODULE_ADDRESS_LISTS = {"monitoring"}

# VLANs (VyOS zones) that are retired. Not emitted at all: no SVI, DHCP, DNS
# records into the subnet, NAT, or any zone pair to or from them.
RETIRED_ZONES = {"ad-110", "app-720", "bastion-410", "iLO-550", "pve-11", "k8s-120"}

# Address groups for hosts that are retired. Every rule that names one is
# dropped, the group is not emitted, and dstnat exemptions for its members go.
RETIRED_ADDRESS_GROUPS = {
    # Named for a UDM Pro, but 172.16.1.1 is the EdgeRouter, which retires.
    "udmpro": "stale UDM Pro entry pointing at the EdgeRouter",
    "blue_iris": "Blue Iris NVR, replaced by Frigate",
}

# --------------------------------------------------------------------------
# Camera policy. The camera VLAN is untrusted: cameras may only reach the
# router's DHCP/DNS/NTP, nothing on 910 may reach them except Frigate's RTSP
# pulls, and everything else on 910 talks to Frigate rather than to a camera.
# --------------------------------------------------------------------------
CAMERA_ZONE = "seccam-610"
EXTRA_ZONE_RULES = {
    ("unifi-frontend-910", CAMERA_ZONE): [{
        "description": "Frigate (WSL cluster) RTSP pulls from the cameras",
        "protocol": "tcp_udp",
        "dst_port": "554",
        "src_address_list": "frigate",
        "dst_address_list": "security_cameras",
    }],
}

# Static DNS records that pointed at container IPs. None drops the record;
# a string retargets it. Per-name overrides win over per-address ones.
DNS_RECORD_RETARGET_BY_NAME: dict[str, str | None] = {}
DNS_RECORD_RETARGET_BY_ADDRESS = {
    "10.10.53.8": None,   # haproxy-frontend -> old k8s ingress (gone)
    "10.10.53.9": None,   # haproxy-authenticated -> old k8s ingress (gone)
    "10.10.53.10": UNIFI_CONTROLLER_HOME,  # `unifi` inform host; unpoller uses https://unifi:8443
}

# Static DNS records for hosts that no longer exist. Dropped, with the reason
# recorded in the locals footer.
DNS_RECORD_DROP = {
    "sce-vyos01.tnwks.local": "VyOS router, retired",
    "sce-er01.tnwks.local": "EdgeRouter, retired",
    "sce-pve01-ceph.tnwks.local": "Proxmox Ceph network, retired",
    "sce-pve02-ceph.tnwks.local": "Proxmox Ceph network, retired",
    "sce-pve03-ceph.tnwks.local": "Proxmox Ceph network, retired",
    "s3.tnwks.local": "Proxmox Ceph network, retired",
    "uisp.tnwks.us": "UISP ran as a VyOS container; no UISP server runs now",
    "unms.tnwks.us": "UISP ran as a VyOS container; no UISP server runs now",
}

# Static DNS records with no VyOS source.
EXTRA_DNS_RECORDS = {
    "sce-rtr01.tnwks.local": "10.10.91.1",
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
# The RB5009 replaces sce-er01 too, so the transit hop disappears.
# 172.16.1.252 and .254 are both dead (`ip neigh` FAILED, 100% ping loss; see
# docs/edgerouter-discovery.md), yet both routers still route production
# subnets at them. Anything pointing at a dead next-hop is a bug, not a config to port.
# --------------------------------------------------------------------------
DEAD_NEXT_HOPS = {"172.16.1.252", "172.16.1.254"}

# The old upstream router. Its static default is not ported as a VyOS route;
# instead it becomes the module's upstream_gateway (distance 10), so the RB5009
# can replace VyOS first while the ERL still holds the WAN, and the WAN DHCP
# lease takes over the default automatically when the modem moves to ether1.
RETIRED_UPSTREAM = "172.16.1.1"
UPSTREAM_GATEWAY = RETIRED_UPSTREAM

# VyOS reached the internet through transit-10, so `X -> transit-10` rulesets
# are its internet-egress policy. The WAN port joins this zone's list.
OUTSIDE_ZONE = "transit-10"

# Out-of-band management. Standalone port, NOT in the LAN bridge: the bootstrap
# script puts the REST API address here, and bridging it would invalidate that
# address on the first apply. Its own subnet, used nowhere else.
MGMT = {"interface": "ether8", "address": "192.168.88.1/24"}

# Switch management. The Aruba's management address (10.98.0.251) is on the
# trunk's untagged native VLAN, where VyOS has 10.98.0.1/24 on bare eth1. That
# address is missing from vyos-config, so it is declared here. Untagged only on
# the uplink (ether2, cabled to Aruba GE0/0/23).
NATIVE_VLAN = {
    "zone": "switch-mgmt",
    "address": "10.98.0.1/24",
    "ports": ["ether2"],
    "comment": "Aruba switch management (untagged on the uplink)",
}

# Management access model: people reach sensitive systems (switch, UniFi and
# UISP management, the router itself) only over the admin VPN. Services keep
# their own narrow paths (UniFi controller <-> APs, mktxp -> router API).
# Break-glass: docs/network-access.md.
ADMIN_ZONE = "vpn"  # wg01

# Admin peers on other WireGuard listeners, by peer name. They get the same
# management access as wg01, scoped to their own tunnel address, so a shared
# listener (wg02 also carries a non-admin phone) does not grant it wholesale.
ADMIN_PEERS = {"vpn-mobile": ["it-mobile"]}

# WireGuard peer public keys that replace the ones in vyos-config, keyed
# "<interface>/<peer>". wg01 never ran on VyOS, so its client keys were never
# in use; peers listed here were re-keyed for the RB5009. Public keys only.
WG_PEER_PUBLIC_KEYS = {
    "wg01/it-pc01": "G50MJOM94BeGaX9wVFvChFDOy8YAbu32MpTIVSctsSE=",  # WSL on the desktop
}

# Admin SSH public keys added on top of the ones ported from VyOS (comment
# stripped). The VyOS-era RSA key stays for any machine that still has it.
EXTRA_ADMIN_SSH_KEYS = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF6lX74KwH2/4FQHwIwKUHhsP9CxnvliHLKxcVPTLD2B",  # WSL on the desktop
]

# Zones that hold management interfaces: reachable by people only over the
# admin VPN or from an admin peer.
SENSITIVE_ZONES = ["switch-mgmt", "unifi-mgmt-900", "UISP-140"]

# Who may reach switch management.
NATIVE_VLAN_ACCESS = [ADMIN_ZONE]
BRIDGE_PORTS = ["ether2", "ether3", "ether4", "ether5", "ether6", "ether7", "sfp-sfpplus1"]

# Remote syslog: vector-aggregator on the WSL cluster, reached through the
# Windows host's UDP relay, same as flow-collector. UDP, not VyOS's TCP:
# RouterOS writes TCP syslog with no framing at all (no newline, no octet
# count), so a receiver cannot split the stream into messages.
SYSLOG_REMOTE = WSL_HOST
SYSLOG_PROTOCOL = "udp"

# Source NAT moves off the ERL onto the RB5009's WAN port. VyOS had its
# masquerade rule commented out precisely because the ERL was doing it.
WAN_INTERFACE = "ether1"

# DDNS: the Cloudflare A record the WireGuard clients use as their endpoint,
# which VyOS's `cloudflare-ddns` container kept current. The record must already
# exist; the router updates it by id. Host and domain come from SOPS.
DDNS = {
    "interval": "5m",
    "zone_id": "42d48ba8c41db9079e8eed11ef0250cb",
    "record_id": "b0b3f0ae9b559627df2814e83cd06f39",
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


def routeros_duration(seconds: int) -> str:
    """Format a duration the way RouterOS stores it (1d, 12h, 30m, 45s), so a
    value read back from the router matches the config and does not diff."""
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return "%d%s" % (seconds // size, unit)
    return "%ds" % seconds


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
        else:
            # Anything else (network-group, source port, state, icmp,
            # inbound-interface, ...) changes what the rule matches. Silently
            # dropping it would emit a broader rule than VyOS had, so record it
            # and refuse to emit the rule (see build_zone_policies).
            rule.setdefault("_unknown", []).append(rest)
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
                net["lease_time"] = routeros_duration(seconds)
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
        # Name-based drops (addAction("zip", DropAction())) become NXDOMAIN
        # records covering the name and everything under it.
        for name in re.findall(r'^\s*addAction\(\s*["\']([A-Za-z0-9.-]+)["\']\s*,\s*DropAction\(\)', dtext, re.M):
            if re.match(r"^[0-9./]+$", name):
                continue  # subnet catch-alls, not names
            key = "drop_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
            dns["static_records"][key] = {
                "name": name, "type": "NXDOMAIN", "match_subdomain": True,
                "comment": "dnsdist DropAction",
            }

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


def build_zone_policies(
    rulesets: dict, pairs: dict, zone_iface: dict, port_lists: dict, vlans: dict
) -> tuple[dict, dict, list[str]]:
    """Turn VyOS rulesets into RouterOS zone-pair policies plus input rules.

    Returns (zone_policies, input_rules, notes).
    """
    policies: "OrderedDict[str, dict]" = OrderedDict()
    # (from, to) -> policy key. One RouterOS pair per directed zone pair: two
    # keys for the same pair would each carry their own terminating default,
    # and the first one's drop would shadow every accept in the second.
    pair_keys: dict[tuple[str, str], str] = {}
    input_rules: "OrderedDict[str, dict]" = OrderedDict()
    notes: list[str] = []
    seq = 0
    input_seq = 0

    def default_of(ruleset: str) -> str:
        return rulesets.get(ruleset, {}).get("meta", {}).get("default-action", "drop")

    def get_pair(from_zone: str, to_zone: str, comment: str, native_default: str | None = None) -> dict:
        """Find or create the policy for a directed pair.

        native_default is the VyOS default-action of the ruleset that actually
        binds this pair. Retargeted container rules join a pair without one, and
        must not override it.
        """
        nonlocal seq
        key = pair_keys.get((from_zone, to_zone))
        if key is None:
            seq += 10
            key = f"{seq:03d}-{from_zone}-{to_zone}"
            pair_keys[(from_zone, to_zone)] = key
            policies[key] = {"from": from_zone, "to": to_zone, "comment": comment, "rules": []}
        pol = policies[key]
        if native_default is not None:
            pol["comment"] = comment
            if native_default == "accept":
                pol["default_action"] = "accept"
        return pol

    def emit_variants(rule: dict, num: int, ruleset: str) -> list[dict]:
        """Expand one VyOS rule into RouterOS-legal variants."""
        if rule.get("_unknown"):
            raise SystemExit(
                f"error: {ruleset} rule {num} uses match keys this generator does not "
                f"translate: {rule['_unknown']}. Emitting it without them would be "
                f"broader than VyOS; teach parse_firewall_rules about them first."
            )
        base = {"comment": rule.get("description") or f"rule {num}"}
        for key in ("src_address_list", "dst_address_list", "src_address", "dst_address"):
            if key in rule:
                base[key] = rule[key]

        if "dst_port_list" in rule:
            members = port_lists.get(rule["dst_port_list"], [])
            if len(members) > ROUTEROS_MAX_DST_PORTS:
                # RouterOS rejects >15 dst-port entries, and a port-group renders
                # into one dst-port string, so inline and split it.
                port_chunks = chunk_ports(members)
                notes.append(
                    f"{ruleset} rule {num}: port-group {rule['dst_port_list']} has "
                    f"{len(members)} entries (RouterOS max {ROUTEROS_MAX_DST_PORTS}); "
                    f"inlined and split across {len(port_chunks)} rules"
                )
            else:
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

    def add_dns_input(from_zone: str, rule: dict, num: int, ruleset: str) -> None:
        nonlocal input_seq
        for variant in emit_variants(rule, num, ruleset):
            input_seq += 10
            entry = {"comment": f"DNS from {from_zone}" + (f" (was {ruleset})" if ruleset else "")}
            if from_zone == OUTSIDE_ZONE:
                # The outside zone's list also holds the WAN port, so matching
                # the list would answer DNS on the internet. Pin to the SVI.
                entry["in_interface"] = "bridge-lan-vlan%s" % vlans[from_zone]["vlan_id"]
            else:
                # Scoped to the originating zone, so it can never be satisfied
                # from the WAN. The EdgeRouter's 0.0.0.0 DNS bind is exactly
                # what this avoids.
                entry["in_interface_list"] = from_zone
            for key in ("protocol", "dst_port"):
                if key in variant:
                    entry[key] = variant[key]
            input_rules[f"{input_seq:03d}-dns-{from_zone}-{entry.get('protocol', 'any')}"] = entry

    dns_rule = {"protocol": "tcp_udp", "dst_port": "domain,domain-s", "description": "accept_dns"}

    for ruleset in sorted(rulesets):
        data = rulesets[ruleset]
        default_action = default_of(ruleset)
        from_zone, to_zone = pairs.get(ruleset, (None, None))

        if from_zone is None:
            if data["rules"]:
                notes.append(
                    f"{ruleset}: orphaned ruleset, no `firewall zone ... from` binding "
                    f"in the VyOS config, so it never applied (pre-existing VyOS drift)"
                )
            continue

        # --- containers zone disposition -----------------------------------
        if to_zone in UNPORTED_ZONES:
            if default_action == "accept":
                # e.g. vpn-containers: everything to the containers was allowed,
                # which at minimum means DNS. The rest of the container access
                # is covered by this zone's own pair to the successor VLAN.
                add_dns_input(from_zone, dns_rule, 0, ruleset)
                notes.append(
                    f"{ruleset} (default-action accept): DNS moved to the input chain; "
                    f"other container access follows {from_zone} -> {CONTAINER_ZONE_SUCCESSOR}"
                )
            for num in sorted(data["rules"]):
                rule = data["rules"][num]
                if rule.get("action") != "accept":
                    continue
                if rule.get("dst_port", "") in DNS_PORT_SPECS:
                    # The resolver now runs ON the router, so this stops being
                    # forwarded traffic and becomes an input-chain accept.
                    add_dns_input(from_zone, rule, num, ruleset)
                    notes.append(
                        f"{ruleset} rule {num} (accept_dns): moved to the input chain; "
                        f"native RouterOS DNS replaces the dnsdist container"
                    )
                    continue
                retarget = dict(rule)
                grp = retarget.get("dst_address_list")
                if grp is None and retarget.get("dst_port_list") in CONTAINER_PORT_GROUP_DESTINATIONS:
                    grp = CONTAINER_PORT_GROUP_DESTINATIONS[retarget["dst_port_list"]]
                if grp in HAPROXY_ONLY_GROUPS or grp not in set(CONTAINER_ADDRESS_GROUPS) | set(CONTAINER_ADDRESS_GROUPS.values()):
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED; it targeted {grp or 'an unnamed container'}, "
                        f"which only existed for haproxy (not ported, its backends are gone)"
                    )
                    continue
                retarget["dst_address_list"] = CONTAINER_ADDRESS_GROUPS.get(grp, grp)
                if from_zone == CONTAINER_ZONE_SUCCESSOR:
                    # Same VLAN as the workload's new home: switched locally, the
                    # router never sees it, so a forward rule would be dead config.
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED as a router rule; {from_zone} and the "
                        f"new home of {grp} ({CONTAINER_ZONE_SUCCESSOR}) are the same VLAN"
                    )
                    continue
                pol = get_pair(from_zone, CONTAINER_ZONE_SUCCESSOR,
                               f"{from_zone} -> {CONTAINER_ZONE_SUCCESSOR}")
                for variant in emit_variants(retarget, num, ruleset):
                    variant["comment"] += f" (was {ruleset})"
                    pol["rules"].append(variant)
                notes.append(
                    f"{ruleset} rule {num}: retargeted to {from_zone} -> "
                    f"{CONTAINER_ZONE_SUCCESSOR} dst {retarget['dst_address_list']}"
                )
            continue

        if from_zone in UNPORTED_ZONES:
            src_group = CONTAINER_SOURCE_RULESETS.get(ruleset)
            for num in sorted(data["rules"]):
                rule = data["rules"][num]
                if rule.get("action") != "accept":
                    continue
                if src_group is None:
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED; haproxy -> backend traffic, and "
                        f"haproxy is not ported (its backends are gone)"
                    )
                    continue
                if to_zone == CONTAINER_ZONE_SUCCESSOR:
                    notes.append(
                        f"{ruleset} rule {num}: DROPPED as a router rule; source and "
                        f"destination are both in {CONTAINER_ZONE_SUCCESSOR}"
                    )
                    continue
                # Scope the source to the container's new address, not the whole
                # successor VLAN. VyOS only ever allowed the container itself.
                retarget = dict(rule, src_address_list=src_group)
                pol = get_pair(CONTAINER_ZONE_SUCCESSOR, to_zone,
                               f"{CONTAINER_ZONE_SUCCESSOR} -> {to_zone}")
                for variant in emit_variants(retarget, num, ruleset):
                    variant["comment"] += f" (was {ruleset})"
                    pol["rules"].append(variant)
                notes.append(
                    f"{ruleset} rule {num}: source retargeted to {src_group} in "
                    f"{CONTAINER_ZONE_SUCCESSOR}"
                )
            continue

        # --- normal zone pair ----------------------------------------------
        rules_out = []
        for num in sorted(data["rules"]):
            rule = data["rules"][num]
            if rule.get("action") != "accept":
                raise SystemExit(
                    f"error: {ruleset} rule {num} has action={rule.get('action')}; only "
                    f"accept rules are translated. Add support before regenerating."
                )
            rules_out.extend(emit_variants(rule, num, ruleset))

        if not rules_out and default_action != "accept":
            # 110 of the 140 VyOS rulesets are empty default-drop pairs. The
            # module's terminal forward drop expresses that; no chain needed.
            continue

        pol = get_pair(from_zone, to_zone, data["meta"].get("description", ruleset),
                       native_default=default_action)
        pol["rules"].extend(rules_out)

    for from_zone in NATIVE_VLAN_ACCESS:
        get_pair(from_zone, NATIVE_VLAN["zone"], f"From {from_zone} to {NATIVE_VLAN['zone']}",
                 native_default="accept")
    # The switch resolves through the router like every other internal network.
    add_dns_input(NATIVE_VLAN["zone"], dns_rule, 0, "")

    for (from_zone, to_zone), extra_rules in EXTRA_ZONE_RULES.items():
        pol = get_pair(from_zone, to_zone, f"From {from_zone} to {to_zone}")
        for num, rule in enumerate(extra_rules, start=1):
            pol["rules"].extend(emit_variants(rule, num, f"{from_zone}-{to_zone} (camera policy)"))

    # Router-destined services. VyOS left input unfiltered (no local zone), so
    # these are additions rather than ports. They are required for the router to
    # stay reachable and functional once the input chain ends in a drop. The
    # out-of-band mgmt port is handled by the module itself (var.mgmt).
    input_seq = max(input_seq, 500)
    for offset, extra in enumerate([
        {"comment": "ICMP for path MTU discovery and diagnostics", "protocol": "icmp"},
        # LAN-only: never answer DHCP/NTP/DNS on the WAN.
        {"comment": "DHCP requests from LAN clients", "protocol": "udp",
         "dst_port": "67,68", "in_interface_list": "lan"},
        {"comment": "NTP server mode for LAN clients only (never WAN)", "protocol": "udp",
         "dst_port": "123", "in_interface_list": "lan"},
        # Router management only over the admin VPN (and the out-of-band port,
        # which the module handles). Never from a LAN VLAN or the WAN.
        {"comment": "SSH/REST/Winbox from the admin VPN (wg01)", "protocol": "tcp",
         "dst_port": "22,443,8291", "in_interface_list": ADMIN_ZONE},
    ] + [
        {"comment": f"SSH/REST/Winbox from admin peers on {zone}", "protocol": "tcp",
         "dst_port": "22,443,8291", "in_interface_list": zone, "src_address_list": "admin_peers"}
        for zone in sorted(ADMIN_PEERS)
    ] + [
        # LAN hosts (the desktop) join the admin VPN over the LAN.
        {"comment": "WireGuard from the LAN, to join the admin VPN from inside",
         "protocol": "udp", "dst_port": "51820,51821", "in_interface_list": "lan"},
        # The one service that must answer from outside. While the ERL is still
        # upstream it port-forwards 51820/51821 to 172.16.1.250 (this router's
        # transit-10 SVI); afterwards they arrive on ether1. Both are members of
        # the outside zone's list, so one rule covers both stages.
        {"comment": "WireGuard listeners (ERL port-forward, then WAN)",
         "protocol": "udp", "dst_port": "51820,51821", "in_interface_list": OUTSIDE_ZONE},
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
    admin_keys += [k for k in EXTRA_ADMIN_SSH_KEYS if k not in admin_keys]
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

    # Retired zones: drop the VLAN and unbind every ruleset to or from it, before
    # any translation, so nothing is emitted on their behalf.
    retired_notes: list[str] = []
    retired_nets = []
    for zone in sorted(RETIRED_ZONES):
        vlan = vlans.pop(zone, None)
        if vlan and vlan.get("address"):
            retired_nets.append(ipaddress.ip_interface(vlan["address"]).network)
        retired_notes.append("zone %s: retired; VLAN, DHCP and every pair to or from it are not emitted" % zone)
    for ruleset in [r for r, (f, t) in pairs.items() if f in RETIRED_ZONES or t in RETIRED_ZONES]:
        del pairs[ruleset]
    dhcp = {z: n for z, n in dhcp.items() if z not in RETIRED_ZONES}

    def in_retired_net(address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in net for net in retired_nets)

    for key in [k for k, r in dns["static_records"].items() if in_retired_net(r.get("address", ""))]:
        rec = dns["static_records"].pop(key)
        retired_notes.append("dns: %s -> %s DROPPED; address is in a retired VLAN" % (rec["name"], rec["address"]))

    # Retired hosts: drop their groups and every rule that names one.
    retired_members = set()
    for group, why in RETIRED_ADDRESS_GROUPS.items():
        retired_members.update(address_lists.pop(group, {}).values())
        for name, data in rulesets.items():
            for num in [n for n, r in data["rules"].items()
                        if group in (r.get("src_address_list"), r.get("dst_address_list"))]:
                del data["rules"][num]
                retired_notes.append("%s rule %s: DROPPED; it names %s (%s)" % (name, num, group, why))

    # Admin peers: resolve each named peer's tunnel address and give it the
    # same reach into the sensitive zones as the admin VPN.
    iface_of_zone = dict(zone_iface)
    admin_peer_members = OrderedDict()
    for zone, peer_names in ADMIN_PEERS.items():
        peers = wireguard.get(iface_of_zone.get(zone, ""), {}).get("peers", {})
        for name in peer_names:
            for addr in peers.get(name, {}).get("allowed_address", []):
                # RouterOS stores a single host without /32.
                host = addr[:-3] if addr.endswith("/32") else addr
                admin_peer_members[name + "_" + host.replace(".", "_")] = host
        for target in SENSITIVE_ZONES:
            EXTRA_ZONE_RULES.setdefault((zone, target), []).append({
                "description": "admin peers (%s)" % ", ".join(peer_names),
                "src_address_list": "admin_peers",
            })
    if admin_peer_members:
        address_lists["admin_peers"] = admin_peer_members

    zone_policies, input_rules, notes = build_zone_policies(
        rulesets, pairs, zone_iface, port_lists, vlans
    )
    notes.extend(retired_notes)

    # Address and port lists nothing references any more (their rules went
    # with a retired zone or host) are not emitted.
    referenced_addr, referenced_port = set(), set()
    for rule in [r for p in zone_policies.values() for r in p["rules"]] + list(input_rules.values()):
        referenced_addr.update(filter(None, (rule.get("src_address_list"), rule.get("dst_address_list"))))
        if rule.get("dst_port_list"):
            referenced_port.add(rule["dst_port_list"])
    for name in sorted(set(address_lists) - referenced_addr - MODULE_ADDRESS_LISTS):
        del address_lists[name]
        notes.append("address list %s: not emitted, no remaining rule references it" % name)
    for name in sorted(set(port_lists) - referenced_port):
        del port_lists[name]
        notes.append("port list %s: not emitted, no remaining rule references it" % name)
    notes.extend(dns_notes)
    notes.extend(admin_notes)

    # Address lists that describe where container workloads went, plus the
    # router-admin list. These replace any same-named VyOS group.
    for name, members in NEW_ADDRESS_LISTS.items():
        if name in address_lists:
            notes.append(
                "address list %s: VyOS members %s replaced with %s (workload moved)"
                % (name, list(address_lists[name].values()), list(members.values()))
            )
        address_lists[name] = OrderedDict(members)

    # Static DNS records that pointed at container IPs.
    for key in list(dns["static_records"]):
        rec = dns["static_records"][key]
        addr = rec.get("address")
        if rec["name"] in DNS_RECORD_RETARGET_BY_NAME:
            target = DNS_RECORD_RETARGET_BY_NAME[rec["name"]]
        elif addr in DNS_RECORD_RETARGET_BY_ADDRESS:
            target = DNS_RECORD_RETARGET_BY_ADDRESS[addr]
        else:
            continue
        if target is None:
            del dns["static_records"][key]
            notes.append(
                "dns: %s -> %s DROPPED; that was a haproxy container, and the app "
                "is served on *.internal.tnwks.us by the WSL ingress now" % (rec["name"], addr)
            )
        else:
            rec["address"] = target
            notes.append("dns: %s retargeted %s -> %s (container moved)" % (rec["name"], addr, target))

    for key in list(dns["static_records"]):
        rec = dns["static_records"][key]
        if rec["name"] in DNS_RECORD_DROP:
            del dns["static_records"][key]
            notes.append("dns: %s DROPPED (%s)" % (rec["name"], DNS_RECORD_DROP[rec["name"]]))
    for name, addr in EXTRA_DNS_RECORDS.items():
        key = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
        dns["static_records"][key] = {"name": name, "type": "A", "address": addr, "comment": "generator"}

    # Things added to vyos-config after this generator was written. Not ported
    # by the generator; called out so they are not lost silently.
    system_text = (parts / "system.sh").read_text()
    if "flow-accounting" in system_text or "sflow" in system_text:
        notes.append(
            "system: VyOS flow export (sFlow, for monitoring/flow-collector) is not "
            "generated; the root module's flow_export (IPFIX) replaces it"
        )
    if "task-scheduler" in system_text:
        notes.append(
            "system: VyOS task-scheduler (dhcp-leases-textfile.py -> node-exporter "
            "dhcp_lease_info, used for flow-collector client names) has no RouterOS "
            "port; client names need a RouterOS lease exporter (e.g. mktxp)"
        )
    notes.append(
        "dns: blocking and split-horizon now apply to EVERY VLAN; dnsdist only "
        "sent 10.10.91.0/24 and 10.10.31.0/24 through blocky. RouterOS adlists "
        "are global"
    )

    # --- EdgeRouter consolidation: prune routes that no longer make sense ----
    kept_routes = {}
    for label, route in routes.items():
        gw = route["gateway"]
        dst = route["dst_address"]
        if gw in DEAD_NEXT_HOPS:
            notes.append(
                "route %s via %s DROPPED: next-hop is dead (ip neigh FAILED, 100%% "
                "ping loss). Both VyOS and the EdgeRouter still pointed "
                "production subnets at it" % (dst, gw)
            )
            continue
        if dst == "0.0.0.0/0" and gw == RETIRED_UPSTREAM:
            notes.append(
                "route 0.0.0.0/0 via %s: not a static route; becomes "
                "upstream_gateway (distance 10) for the staged cutover, and the "
                "WAN DHCP lease (distance 1) takes over when the modem moves" % gw
            )
            continue
        kept_routes[label] = route
    routes = kept_routes

    # NAT: rewrite VyOS interface references to RouterOS VLAN interface names.
    dstnat = {}
    for num, rule in sorted(nat.items()):
        zone = rule.pop("_zone", None)
        if zone not in vlans:
            why = "zone is retired" if zone in RETIRED_ZONES else "zone %s is not a ported VLAN" % zone
            notes.append("nat rule %s (%s): DROPPED; %s" % (num, rule.get("comment", ""), why))
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
        # An exemption for a retired host ("!10.10.60.210") has nothing to exempt.
        if entry.get("src_address", "").lstrip("!") in retired_members:
            notes.append("nat rule %s (%s): exemption for retired host %s removed"
                         % (num, entry["comment"], entry.pop("src_address").lstrip("!")))

        # A redirect to a dead host is a black hole, not a feature. The VyOS
        # transit-10 NTP rule pointed at 172.16.1.254, which means NTP for that
        # zone has been silently failing; do not carry the bug forward.
        if entry["to_address"] in DEAD_NEXT_HOPS:
            notes.append(
                "nat rule %s (%s) DROPPED: redirect target %s is dead, so this "
                "rule black-holes traffic today"
                % (num, entry["comment"], entry["to_address"])
            )
            continue

        # Forced DNS pointed at the dnsdist/blocky containers. The resolver is
        # the router now, so redirect (to the router on the ingress interface).
        # The `!container` destination exclusion has nothing left to exclude.
        if entry["to_address"] in CONTAINER_DNS_ADDRESSES:
            notes.append(
                "nat rule %s (%s): dst-nat to %s (DNS container) became a redirect "
                "to the router" % (num, entry["comment"], entry["to_address"])
            )
            entry["action"] = "redirect"
            del entry["to_address"]
            if entry.get("dst_address", "").lstrip("!") in CONTAINER_DNS_ADDRESSES:
                del entry["dst_address"]

        for proto in expand_protocol(entry["protocol"]):
            dstnat["%s-%s" % (num, proto)] = dict(entry, protocol=proto)

    # WireGuard: strip private keys, main.tf injects them from SOPS.
    for key, pub in WG_PEER_PUBLIC_KEYS.items():
        iface, peer = key.split("/", 1)
        if peer not in wireguard.get(iface, {}).get("peers", {}):
            raise SystemExit("error: WG_PEER_PUBLIC_KEYS names unknown peer %s" % key)
        wireguard[iface]["peers"][peer]["public_key"] = pub
        notes.append("wireguard: %s re-keyed for the RB5009 (WG_PEER_PUBLIC_KEYS)" % key)

    wg_out = {}
    iface_zone = {v: k for k, v in zone_iface.items()}
    for name, iface in sorted(wireguard.items()):
        wg_out[name] = {
            "zone": iface_zone.get(name, name),
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
    # RouterOS stores loose-tcp-tracking as true/false; writing "yes" would
    # read back as "true" and diff on every plan.
    conntrack = {"enabled": "yes", "loose_tcp_tracking": "true"}

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

    # The router's own name, matching the bootstrap script, not VyOS's
    # sce-vyos01. Its DNS record is in EXTRA_DNS_RECORDS.
    vyos_identity = system.get("identity", "sce-vyos01")
    identity = "sce-rtr01"
    if vyos_identity != identity:
        notes.append("system: identity is %s, not the VyOS name %s" % (identity, vyos_identity))

    body = ["locals {\n"]
    body.append(assign("identity", identity))
    body.append(assign("domain", system.get("domain", "tnwks.local")))
    body.append(assign("timezone", system.get("timezone", "America/Los_Angeles")))
    body.append("\n")
    body.append(assign("wan_interface", WAN_INTERFACE,
                       "ether1 is WAN, ether8 is out-of-band mgmt, the rest form the\n"
                       "VLAN trunk bridge."))
    body.append(assign("lan_trunk_interface", "bridge-lan"))
    body.append(assign("bridge_name", "bridge-lan"))
    body.append(assign("bridge_ports", BRIDGE_PORTS))
    body.append(assign("mgmt", MGMT,
                       "Never bridged: this is the address Terraform reaches the router on."))
    body.append(assign("native_vlan", NATIVE_VLAN,
                       "Untagged on the uplink only; the Aruba's management lives here."))
    body.append("\n")
    body.append(assign("outside_zone", OUTSIDE_ZONE,
                       "VyOS's internet egress went via transit-10; the WAN port joins it."))
    body.append(assign("upstream_gateway", UPSTREAM_GATEWAY,
                       "EdgeRouter Lite, still upstream during the staged cutover. Set to\n"
                       "null once the modem is on ether1 and the ERL is retired."))
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
        old_remote = system["syslog"].get("remote")
        if old_remote != SYSLOG_REMOTE:
            notes.append(
                "syslog: target %s (Vector on the old cluster, gone) -> %s:%s, the "
                "Windows host's UDP relay to vector-aggregator (tnwks-lan-bridge@syslog)"
                % (old_remote, SYSLOG_REMOTE, system["syslog"].get("remote_port", 6001))
            )
            system["syslog"]["remote"] = SYSLOG_REMOTE
        system["syslog"]["remote_protocol"] = SYSLOG_PROTOCOL
        body.append("\n")
        body.append(assign("syslog", system["syslog"],
                           "Syslog to vector-aggregator on the WSL cluster, over UDP: RouterOS\n"
                           "TCP syslog has no message framing."))
    body.append("\n")
    body.append(assign("monitoring_source_list", "monitoring",
                       "Address list allowed to reach the router's read-only API (mktxp)."))
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
                       "Replaces VyOS's cloudflare-ddns container. RouterOS has no Cloudflare\n"
                       "client, so the module renders a script + scheduler onto the router;\n"
                       "see modules/mikrotik/ddns.tf.\n"
                       "LOAD-BEARING: the WireGuard client configs use this hostname as\n"
                       "their endpoint, so a stale record breaks remote VPN access at the\n"
                       "next WAN address change. Host, domain and API token come from SOPS."))
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
