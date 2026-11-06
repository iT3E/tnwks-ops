#!/usr/bin/env bash
# Move a Talos-in-Docker cluster's node containers onto a recreated Docker
# network: Stage B of the routed multi-site network (docs/wsl/multi-site.md).
#
#   talos/wsl/recreate-network.sh [--dry-run] [--yes] <cluster> <bridge>   # to nat-unprotected
#   talos/wsl/recreate-network.sh [--dry-run] [--yes] --rollback <cluster> [<bridge>] # back to nat
#
#   talos/wsl/recreate-network.sh homelab-wsl-02 br-site2                 # on WSL-02's distro
#   talos/wsl/recreate-network.sh homelab-wsl br-site1                    # on DESKTOP-HKE05KI's
#
# Docker network options are immutable, so the network is removed and
# created again with the same name, subnet, gateway and labels and the new
# options: com.docker.network.bridge.gateway_mode_ipv4=nat-unprotected,
# enable_ip_masquerade=false and a fixed bridge.name (talos_clusters'
# docker_network in inventory/group_vars/all/talos.yml). The node containers
# aren't recreated: they're stopped, disconnected, reconnected with
# `--ip <their address>` and started, so everything else about them (volumes,
# binds, published 127.0.0.1 ports, restart policy) stays as it is. For the
# cluster it's a restart of every node: about 90 s until Services answer
# (rehearsed 2026-11-06), plus the apps' own start.
#
# In order:
#   1. checks: the nodes and their addresses, nothing else on the network,
#      every bind source present (site 1 worker-2: /dev/bus/usb, the Z-Stick
#      at ZSTICK, default its /dev/serial/by-id path, and E:;
#      Docker would otherwise create an empty directory in their place)
#   2. the plan in ~/stage-b/<cluster>-<time>/ (network, nodes, HostConfig and
#      Mounts of each node, no env: USERDATA is the machine config)
#   3. the new options on a scratch network (SCRATCH_SUBNET, default
#      10.250.250.0/24) with the same bridge name, then removed: an option
#      Docker rejects stops the run here, before anything is down
#   4. docker stop -t 120 (Talos shuts down gracefully in about 30 s),
#      disconnect, rm, create, connect --ip, start (control plane first)
#   5. `systemctl reload tnwks-site-routing` where it tracks this network
#      (the bridge name changed), then
#      wait for the Talos API, the Kubernetes API and every node Ready
#      (CLUSTER_TALOSCONFIG / CLUSTER_KUBECONFIG, default
#      ~/.talos/config-<cluster> and ~/.kube/config-<cluster>)
#
# --dry-run does 1-3 and prints the commands of 4. --rollback recreates it
# with only the MTU option, as talosctl made it. The distro's masquerade
# (wsl_site_routing_masquerade) has to be on before the swap to
# nat-unprotected, or the nodes lose internet egress (image pulls fail).
set -euo pipefail

usage() {
  sed -n '4,6p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

dry_run=""
rollback=""
yes=""
args=()
while (($#)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --rollback) rollback=1 ;;
    --yes) yes=1 ;;
    -h | --help) usage ;;
    -*) echo "unknown flag $1" >&2; usage ;;
    *) args+=("$1") ;;
  esac
  shift
done
net="${args[0]:-}"
bridge="${args[1]:-}"
[[ -n "${net}" ]] || usage
# A rollback has no bridge name (Docker's br-<id> again); one given is ignored.
[[ -z "${rollback}" ]] || bridge=""
if [[ -z "${rollback}" && -z "${bridge}" ]]; then usage; fi
if [[ -n "${bridge}" && ${#bridge} -gt 15 ]]; then
  echo "bridge name ${bridge} is longer than 15 characters" >&2
  exit 2
fi

scratch="${SCRATCH_SUBNET:-10.250.250.0/24}"
# Not TALOSCONFIG/KUBECONFIG: the Taskfile points KUBECONFIG at the repo's.
talosconfig="${CLUSTER_TALOSCONFIG:-${HOME}/.talos/config-${net}}"
kubeconfig="${CLUSTER_KUBECONFIG:-${HOME}/.kube/config-${net}}"
zstick="${ZSTICK:-/dev/serial/by-id/usb-0658_0200-if00}"

log() { printf '%s %s\n' "$(date +%T)" "$*"; }
die() { echo "error: $*" >&2; exit 1; }
# Print a command, then run it unless --dry-run.
run() {
  printf '+'; printf ' %q' "$@"; echo
  [[ -n "${dry_run}" ]] || "$@"
}

# --- 1. checks ------------------------------------------------------------------
docker network inspect "${net}" >/dev/null 2>&1 || die "no Docker network ${net}"
subnet="$(docker network inspect -f '{{ (index .IPAM.Config 0).Subnet }}' "${net}")"
gateway="$(docker network inspect -f '{{ with (index .IPAM.Config 0).Gateway }}{{ . }}{{ end }}' "${net}")"
# talosctl 1.14 leaves the gateway out (Docker takes the first address): pin it.
[[ -n "${gateway}" ]] || gateway="${subnet%.*/*}.1"
mtu="$(docker network inspect -f '{{ with index .Options "com.docker.network.driver.mtu" }}{{ . }}{{ end }}' "${net}")"
mtu="${mtu:-1500}"
old_options="$(docker network inspect -f '{{ range $k, $v := .Options }}{{ $k }}={{ $v }} {{ end }}' "${net}")"

new_options=(-o "com.docker.network.driver.mtu=${mtu}")
if [[ -z "${rollback}" ]]; then
  new_options+=(
    -o "com.docker.network.bridge.name=${bridge}"
    -o com.docker.network.bridge.gateway_mode_ipv4=nat-unprotected
    -o com.docker.network.bridge.enable_ip_masquerade=false
  )
fi
want="$(printf '%s\n' "${new_options[@]}" | grep -v '^-o$' | sort | tr '\n' ' ')"
have="$(tr ' ' '\n' <<<"${old_options}" | grep . | sort | tr '\n' ' ')"
if [[ "${want}" == "${have}" ]]; then
  log "${net} already has ${want}: nothing to do"
  exit 0
fi

# The nodes, control plane first (connect and start in that order).
mapfile -t nodes < <(docker ps -a --filter "label=talos.cluster.name=${net}" \
  --format '{{ .Label "talos.type" }} {{ .Names }}' | sort | awk '{print $2}')
((${#nodes[@]} > 0)) || die "no containers labelled talos.cluster.name=${net}"
declare -A node_ip
for c in "${nodes[@]}"; do
  node_ip[${c}]="$(docker inspect -f "{{ with index .NetworkSettings.Networks \"${net}\" }}{{ with .IPAMConfig }}{{ .IPv4Address }}{{ end }}{{ end }}" "${c}")"
  [[ -n "${node_ip[${c}]}" ]] || die "${c} has no fixed address on ${net} (IPAMConfig.IPv4Address)"
done
# Nothing but the nodes on the network (stopped containers included).
others="$(docker ps -a --filter "network=${net}" --format '{{ .Names }}' | grep -vxF -f <(printf '%s\n' "${nodes[@]}") || true)"
[[ -z "${others}" ]] || die "other containers on ${net}: ${others}"

# Bind sources: Docker creates a missing one as an empty directory, which
# would hide an unattached USB device or an unmounted E: from the node.
missing=()
for c in "${nodes[@]}"; do
  while read -r src; do
    [[ "${src}" == /* ]] || continue # a named volume
    if [[ ! -e "${src}" ]]; then
      missing+=("${c}: ${src}")
    elif [[ "${src}" == /dev/bus/usb ]] && ! compgen -G '/dev/bus/usb/*/*' >/dev/null; then
      missing+=("${c}: /dev/bus/usb is empty (no usbip device attached)")
    elif [[ "${src}" == /dev ]] && [[ ! -e "${zstick}" ]]; then
      missing+=("${c}: ${zstick} (Z-Stick not attached: usbipd; ZSTICK= overrides the path)")
    elif [[ "${src}" == /mnt/* ]] && [[ -d "${src}" ]] && [[ -z "$(ls -A "${src}" 2>/dev/null)" ]]; then
      missing+=("${c}: ${src} is empty (drive not mounted?)")
    fi
  done < <(docker inspect -f '{{ range .HostConfig.Binds }}{{ . }}{{ "\n" }}{{ end }}' "${c}" | cut -d: -f1)
done
if ((${#missing[@]})); then
  printf 'missing on the host:\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  die "attach or mount them first"
fi
if [[ -n "${bridge}" ]] && ip link show dev "${bridge}" >/dev/null 2>&1; then
  die "an interface ${bridge} already exists"
fi

# --- 2. the plan ----------------------------------------------------------------
plan="${HOME}/stage-b/${net}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${plan}"
chmod 700 "${plan}"
docker network inspect "${net}" >"${plan}/network.json"
for c in "${nodes[@]}"; do
  echo "${c} ${node_ip[${c}]}"
  docker inspect -f '{{ json .HostConfig }}' "${c}" >"${plan}/${c}.hostconfig.json"
  docker inspect -f '{{ json .Mounts }}' "${c}" >"${plan}/${c}.mounts.json"
done >"${plan}/nodes.txt"
labels=(--label talos.owned=true --label "talos.cluster.name=${net}")
create=(docker network create --driver bridge --subnet "${subnet}" --gateway "${gateway}" "${labels[@]}" "${new_options[@]}" "${net}")
printf '%q ' "${create[@]}" >"${plan}/create.sh"
echo >>"${plan}/create.sh"
log "${net}: ${subnet} gateway ${gateway}, ${#nodes[@]} nodes ($(tr '\n' ' ' <"${plan}/nodes.txt")), plan in ${plan}"
log "options now: ${old_options:-none}"
log "options new: ${want}"

# --- 3. the options on a scratch network -------------------------------------------
scratch_gw="${scratch%.*/*}.1"
if ! out="$(docker network create --driver bridge --subnet "${scratch}" --gateway "${scratch_gw}" \
  "${new_options[@]}" "${net}-check" 2>&1)"; then
  die "Docker refused the options on ${scratch} (SCRATCH_SUBNET= picks another): ${out}"
fi
docker network rm "${net}-check" >/dev/null
log "Docker accepts the options (checked on ${scratch})"

if [[ -z "${dry_run}" && -z "${yes}" ]]; then
  read -r -p "Stop ${net}'s ${#nodes[@]} nodes (about 90 s down)? Type the cluster name: " answer
  [[ "${answer}" == "${net}" ]] || die "not confirmed"
fi

# --- 4. the swap ------------------------------------------------------------------
started=$(date +%s)
# shellcheck disable=SC2329 # run by the ERR trap
recovery() {
  echo "error: stopped half-way. The plan is in ${plan}. To finish by hand:" >&2
  echo "  docker network inspect ${net} || $(cat "${plan}/create.sh")" >&2
  echo "  while read -r c a; do docker network connect --ip \"\$a\" ${net} \"\$c\"; done < ${plan}/nodes.txt" >&2
  echo "  docker start ${nodes[*]}" >&2
  echo "(\"network <id> not found\" on start: the disconnect was skipped; connect --ip by name fixes it.)" >&2
}
[[ -n "${dry_run}" ]] || trap recovery ERR

log "${dry_run:+would run: }stop (graceful, up to 120 s), disconnect, rm, create, connect, start"
run docker stop -t 120 "${nodes[@]}"
for c in "${nodes[@]}"; do run docker network disconnect "${net}" "${c}"; done
run docker network rm "${net}"
run "${create[@]}"
for c in "${nodes[@]}"; do run docker network connect --ip "${node_ip[${c}]}" "${net}" "${c}"; done
run docker start "${nodes[@]}"
trap - ERR

if [[ -n "${dry_run}" ]]; then
  if grep -qx "DOCKER_NETWORK=${net}" /etc/default/tnwks-site-routing 2>/dev/null; then
    echo "+ sudo systemctl reload tnwks-site-routing"
  fi
  log "dry run: nothing changed (the plan in ${plan} is only a record)"
  exit 0
fi
log "nodes started $(($(date +%s) - started)) s after the stop"

# --- 5. after ---------------------------------------------------------------------
# Only where tnwks-site-routing tracks this network (role wsl_site_routing).
if systemctl is-active -q tnwks-site-routing.service 2>/dev/null &&
  grep -qx "DOCKER_NETWORK=${net}" /etc/default/tnwks-site-routing 2>/dev/null; then
  if sudo systemctl reload tnwks-site-routing.service; then
    log "tnwks-site-routing reloaded"
  else
    echo "warning: reload tnwks-site-routing by hand (sudo systemctl reload tnwks-site-routing)" >&2
  fi
fi

wait_for() {
  local what=$1 deadline=$(($(date +%s) + 600))
  shift
  until "$@" >/dev/null 2>&1; do
    (($(date +%s) < deadline)) || { echo "warning: no ${what} after 600 s" >&2; return 1; }
    sleep 2
  done
  log "${what} ($(($(date +%s) - started)) s after the stop)"
}
cp_ip="${node_ip[${nodes[0]}]}"
status=0
if [[ -r "${talosconfig}" ]]; then
  wait_for "Talos API" talosctl --talosconfig "${talosconfig}" -n "${cp_ip}" version --short || status=1
else
  echo "warning: no ${talosconfig}; not waiting for the Talos API" >&2
fi
if [[ -r "${kubeconfig}" ]]; then
  wait_for "Kubernetes API" kubectl --kubeconfig "${kubeconfig}" --request-timeout=3s get --raw /readyz || status=1
  wait_for "every node Ready" kubectl --kubeconfig "${kubeconfig}" wait --for=condition=Ready nodes --all --timeout=5s || status=1
else
  echo "warning: no ${kubeconfig}; not waiting for the Kubernetes API" >&2
fi
log "now: $(docker network inspect -f '{{ range $k, $v := .Options }}{{ $k }}={{ $v }} {{ end }}' "${net}")"
exit "${status}"
