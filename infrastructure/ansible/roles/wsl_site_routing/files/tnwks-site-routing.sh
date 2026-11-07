#!/bin/bash
# tnwks-site-routing — the distro's forwarding rules for its site tunnel
# (wg-site<N> to sce-rtr01). docs/wsl/multi-site.md, "Stage A on the distros".
#
#   up     everything below; idempotent (tnwks-site-routing.service runs it at
#          boot and on reload)
#   route  only the policy rule and the tunnel's route (wg-quick PostUp: the
#          route goes away with the interface)
#   down   remove all of it (the unit's stop)
#
# What it sets, all in the distro's root namespace:
#
# - ip_forward=1 and loose rp_filter (2). Strict would drop LAN clients on the
#   tunnel, whose sources the main table routes out of eth0.
# - Connmark return routing. A connection that arrives on the tunnel (a LAN
#   client on a VIP) gets MARK in its conntrack entry; its replies coming back
#   from the Docker bridge get MARK as a packet mark, and the rule sends marked
#   packets to TABLE, whose default route is the tunnel. Everything else, the
#   nodes' own egress (cameras, internet), keeps the main table's default
#   route. The mark is restored in PREROUTING only, on the bridge: restoring
#   it in OUTPUT steers the distro's own frag-needed ICMP into the tunnel and
#   breaks path MTU discovery (router BGP spike, 2026-11-06).
# - MSS clamping on the tunnel, both directions: LAN clients and nodes both
#   advertise 1460, and the tunnel carries 1420.
# - A forward filter for the tunnel (from DOCKER-USER): it may reach this
#   site's node subnet, pod range and the VIP pool, nothing else. Anything
#   else is dropped. It returns to Docker's own rules rather than accepting.
# - A counted drop for misrouted replies (TNWKS-SR-MISROUTED): a reply to a
#   tunnel connection that would leave by the uplink (the tunnel is down, or
#   the mark was lost), or anything from the VIP pool leaving by the uplink.
#   Without it such a packet leaves eth0 past Docker's masquerade and Windows
#   drops it, invisibly. `iptables -vL TNWKS-SR-MISROUTED` shows the count.
# - Stage B, off by default (MASQUERADE=true): masquerade the node subnet's
#   egress, for when the Docker network no longer does. Like Docker's own
#   rule, by any interface (the uplink, or a tunnel of the host's own such as
#   the desktop's wg01-home, whose peer accepts only the host's address)
#   except the site tunnel, where the router routes the real addresses, and
#   the bridge itself.
#
# The Docker bridge is looked up by the Docker network's name each run, so a
# renamed bridge (Stage B fixes the name) is picked up by the next `up`
# (systemctl reload tnwks-site-routing). Without the network (no cluster yet)
# the bridge rules are left out and the rest still applies.
#
# Settings: /etc/default/tnwks-site-routing (role wsl_site_routing).

set -euo pipefail

CONF=/etc/default/tnwks-site-routing
# shellcheck source=/dev/null
[[ -r "$CONF" ]] && . "$CONF"

: "${WG_IF:?WG_IF unset ($CONF)}"
: "${DOCKER_NETWORK:?DOCKER_NETWORK unset ($CONF)}"
: "${NODE_SUBNET:?NODE_SUBNET unset ($CONF)}"
: "${POD_CIDR:?POD_CIDR unset ($CONF)}"
: "${VIP_POOL:?VIP_POOL unset ($CONF)}"
MARK="${MARK:-0x10000}"
TABLE="${TABLE:-200}"
RULE_PRIORITY="${RULE_PRIORITY:-1000}"
UPLINK="${UPLINK:-eth0}"
MSS="${MSS:-1380}"
MASQUERADE="${MASQUERADE:-false}"

TAG=tnwks-site-routing
C_MARK=TNWKS-SR-MARK           # mangle, from PREROUTING
C_MSS=TNWKS-SR-MSS             # mangle, from FORWARD
C_FWD=TNWKS-SR-FWD             # filter, from DOCKER-USER
C_MISROUTED=TNWKS-SR-MISROUTED # filter, from TNWKS-SR-FWD: the counted drop
C_NAT=TNWKS-SR-NAT             # nat, from POSTROUTING

ipt() { iptables -w "$@"; }

log() { echo "$TAG: $*" >&2; }

# The Linux bridge behind the Docker network: its fixed name if it has one
# (com.docker.network.bridge.name), else Docker's br-<first 12 of the id>.
docker_bridge() {
  local name id
  command -v docker >/dev/null || return 1
  name=$(docker network inspect -f '{{ index .Options "com.docker.network.bridge.name" }}' "$DOCKER_NETWORK" 2>/dev/null) || return 1
  if [[ -z "$name" || "$name" == "<no value>" ]]; then
    id=$(docker network inspect -f '{{ .Id }}' "$DOCKER_NETWORK" 2>/dev/null) || return 1
    name="br-${id:0:12}"
  fi
  ip link show dev "$name" >/dev/null 2>&1 || return 1
  echo "$name"
}

# The jump from a built-in (or Docker's) chain to ours, first in it, once.
ensure_jump() {
  local table=$1 parent=$2 chain=$3
  if ! ipt -t "$table" -C "$parent" -m comment --comment "$TAG" -j "$chain" 2>/dev/null; then
    ipt -t "$table" -I "$parent" 1 -m comment --comment "$TAG" -j "$chain"
  fi
}

remove_jump() {
  local table=$1 parent=$2 chain=$3
  while ipt -t "$table" -D "$parent" -m comment --comment "$TAG" -j "$chain" 2>/dev/null; do :; done
}

remove_chain() {
  local table=$1 chain=$2
  ipt -t "$table" -F "$chain" 2>/dev/null || true
  ipt -t "$table" -X "$chain" 2>/dev/null || true
}

# Our chains' contents, for iptables-restore --noflush: a declared chain is
# created or flushed, and each table is replaced in one commit. The misrouted
# chain isn't declared, so its counter survives a re-run.
rules() {
  local br=$1
  echo "*mangle"
  echo ":$C_MARK - [0:0]"
  echo ":$C_MSS - [0:0]"
  echo "-A $C_MARK -i $WG_IF -m conntrack --ctstate NEW -m comment --comment \"$TAG: connection from the tunnel\" -j CONNMARK --set-xmark $MARK/$MARK"
  if [[ -n "$br" ]]; then
    echo "-A $C_MARK -i $br -m connmark --mark $MARK/$MARK -m comment --comment \"$TAG: reply to a tunnel connection\" -j MARK --set-xmark $MARK/$MARK"
  fi
  echo "-A $C_MSS -i $WG_IF -p tcp -m tcp --tcp-flags SYN,RST SYN -m tcpmss --mss $((MSS + 1)):65535 -m comment --comment \"$TAG: MSS from the tunnel\" -j TCPMSS --set-mss $MSS"
  echo "-A $C_MSS -o $WG_IF -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment \"$TAG: MSS into the tunnel\" -j TCPMSS --clamp-mss-to-pmtu"
  echo "COMMIT"

  echo "*filter"
  echo ":$C_FWD - [0:0]"
  local dst
  for dst in "$NODE_SUBNET" "$POD_CIDR" "$VIP_POOL"; do
    echo "-A $C_FWD -i $WG_IF -d $dst -m comment --comment \"$TAG: tunnel to this site\" -j RETURN"
  done
  echo "-A $C_FWD -i $WG_IF -m comment --comment \"$TAG: tunnel to anything else\" -j DROP"
  echo "-A $C_FWD -o $UPLINK -m connmark --mark $MARK/$MARK -m comment --comment \"$TAG: tunnel reply by the uplink\" -j $C_MISROUTED"
  if [[ -n "$br" ]]; then
    echo "-A $C_FWD -i $br -o $UPLINK -s $VIP_POOL -m comment --comment \"$TAG: VIP by the uplink\" -j $C_MISROUTED"
  fi
  echo "COMMIT"

  echo "*nat"
  echo ":$C_NAT - [0:0]"
  if [[ "$MASQUERADE" == true ]]; then
    echo "-A $C_NAT -o $WG_IF -m comment --comment \"$TAG: site tunnel, real addresses\" -j RETURN"
    if [[ -n "$br" ]]; then
      echo "-A $C_NAT -o $br -m comment --comment \"$TAG: the bridge itself\" -j RETURN"
    fi
    echo "-A $C_NAT -s $NODE_SUBNET -m comment --comment \"$TAG: node egress\" -j MASQUERADE"
  fi
  echo "COMMIT"
}

route_up() {
  if ! ip rule show priority "$RULE_PRIORITY" | grep -q "fwmark $MARK/$MARK lookup $TABLE\b"; then
    while ip rule del priority "$RULE_PRIORITY" 2>/dev/null; do :; done
    ip rule add fwmark "$MARK/$MARK" lookup "$TABLE" priority "$RULE_PRIORITY"
  fi
  # Gone with the interface: wg-quick's PostUp puts it back.
  if ip link show dev "$WG_IF" >/dev/null 2>&1; then
    ip route replace default dev "$WG_IF" table "$TABLE"
  else
    log "$WG_IF doesn't exist yet; its PostUp adds the route"
  fi
}

up() {
  sysctl -qw net.ipv4.ip_forward=1 net.ipv4.conf.all.rp_filter=2 net.ipv4.conf.default.rp_filter=2

  local br=""
  if ! br=$(docker_bridge); then
    br=""
    log "no bridge for Docker network $DOCKER_NETWORK; connmark restore and the VIP check left out until the next run"
  fi

  # Docker creates DOCKER-USER too, and keeps what's in it.
  ipt -t filter -N DOCKER-USER 2>/dev/null || true
  ipt -t filter -N "$C_MISROUTED" 2>/dev/null || true
  if ! ipt -t filter -C "$C_MISROUTED" -m comment --comment "$TAG: misrouted reply" -j DROP 2>/dev/null; then
    ipt -t filter -A "$C_MISROUTED" -m comment --comment "$TAG: misrouted reply" -j DROP
  fi

  rules "$br" | iptables-restore -w --noflush

  ensure_jump mangle PREROUTING "$C_MARK"
  ensure_jump mangle FORWARD "$C_MSS"
  ensure_jump filter DOCKER-USER "$C_FWD"
  ensure_jump nat POSTROUTING "$C_NAT"

  route_up
  log "up: $WG_IF, bridge ${br:-none}, mark $MARK, table $TABLE, masquerade $MASQUERADE"
}

down() {
  remove_jump mangle PREROUTING "$C_MARK"
  remove_jump mangle FORWARD "$C_MSS"
  remove_jump filter DOCKER-USER "$C_FWD"
  remove_jump nat POSTROUTING "$C_NAT"
  remove_chain mangle "$C_MARK"
  remove_chain mangle "$C_MSS"
  remove_chain filter "$C_FWD"
  remove_chain filter "$C_MISROUTED"
  remove_chain nat "$C_NAT"
  while ip rule del priority "$RULE_PRIORITY" 2>/dev/null; do :; done
  ip route flush table "$TABLE" 2>/dev/null || true
  log "down"
}

case "${1:-}" in
  up) up ;;
  route) route_up ;;
  down) down ;;
  *)
    echo "usage: $0 {up|route|down}" >&2
    exit 2
    ;;
esac
