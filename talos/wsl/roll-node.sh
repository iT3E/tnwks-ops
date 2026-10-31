#!/usr/bin/env bash
# Move one Talos-in-Docker node onto another Talos image.
#
#   talos/wsl/roll-node.sh <container> <image>
#   talos/wsl/roll-node.sh homelab-wsl-worker-1 ghcr.io/siderolabs/talos:v1.13.12
#
# Talos refuses `talosctl upgrade` in container mode, so the upgrade is a
# container swap: stop the node, then start a new container from the new
# image with everything else copied from the old one. The named volumes hold
# the node's state (/system/state has the machine config and identity, /var
# has etcd, containerd and kubelet data), so the node comes back as itself
# with its config, including the Kubernetes version tuppr set. The tmpfs
# mounts (/run, /system, /tmp) are required: without a writable /system,
# Talos fails at boot with "mkdir /system/etc: read-only file system".
#
# Copied from the old container: hostname, labels, env (PLATFORM, USERDATA,
# TALOSSKU), network and IPv4 address, published ports, memory and CPU
# limits, restart policy, security options, sysctls, named volumes, and bind
# mounts (worker-2's USB and bulk-storage binds). Draining and waiting for
# Ready are the caller's job (05-talos-upgrade.yml). DRY_RUN=1 prints the
# docker run command and changes nothing.
# shellcheck disable=SC2016 # jq programs, not shell expansions
set -euo pipefail

c="${1:?usage: roll-node.sh <container> <image>}"
image="${2:?usage: roll-node.sh <container> <image>}"

spec="$(docker inspect "${c}" | jq '.[0]')"
j() { jq -r "$1" <<<"${spec}"; }

if [[ "$(j .Config.Image)" == "${image}" ]]; then
  echo "${c}: already on ${image}"
  exit 0
fi

args=(
  --detach --name "${c}" --hostname "$(j .Config.Hostname)"
  --privileged --read-only --cgroupns "$(j .HostConfig.CgroupnsMode)"
  --shm-size "$(j .HostConfig.ShmSize)"
  --memory "$(j .HostConfig.Memory)"
  --cpus "$(j '.HostConfig.NanoCpus / 1e9')"
  --restart "$(j .HostConfig.RestartPolicy.Name)"
)
while read -r o; do args+=(--security-opt "${o/seccomp:/seccomp=}"); done < <(j '(.HostConfig.SecurityOpt // [])[]')
while read -r kv; do args+=(--sysctl "${kv}"); done < <(j '(.HostConfig.Sysctls // {}) | to_entries[] | "\(.key)=\(.value)"')
while read -r kv; do args+=(--label "${kv}"); done < <(j '.Config.Labels | to_entries[] | "\(.key)=\(.value)"')
while read -r kv; do args+=(--env "${kv}"); done < <(j '.Config.Env[]')
# talosctl creates tmpfs through the mount API (shows in .Mounts), docker run
# --tmpfs puts it in .HostConfig.Tmpfs. Always include the three Talos needs.
while read -r m; do args+=(--tmpfs "${m}"); done < <(j '[(.Mounts[] | select(.Type == "tmpfs") | .Destination), ((.HostConfig.Tmpfs // {}) | keys[]), "/run", "/system", "/tmp"] | unique[]')
# Binds first (a recreated worker-2 lists its named volumes there too), then
# any named volume whose destination no bind already covers.
while read -r m; do args+=(--volume "${m}"); done < <(j '
  ((.HostConfig.Binds // []) | map(split(":")[1])) as $bound
  | ((.HostConfig.Binds // [])[]),
    (.Mounts[] | select(.Type == "volume") | select(.Destination as $d | $bound | index($d) | not) | "\(.Name):\(.Destination)")')
while read -r d; do args+=(--device "${d}"); done < <(j '(.HostConfig.Devices // [])[] | "\(.PathOnHost):\(.PathInContainer):\(.CgroupPermissions)"')
while read -r p; do args+=(--publish "${p}"); done < <(j '(.HostConfig.PortBindings // {}) | to_entries[] | .key as $k | .value[] | "\(.HostIp):\(.HostPort):\($k)"')
net="$(j '.NetworkSettings.Networks | keys[0]')"
ip="$(j ".NetworkSettings.Networks[\"${net}\"].IPAMConfig.IPv4Address")"
args+=(--network "${net}" --ip "${ip}")

if [[ -n "${DRY_RUN:-}" ]]; then
  printf 'docker run'; printf ' %q' "${args[@]}" "${image}"; echo
  exit 0
fi

echo "${c}: pulling ${image}"
docker pull -q "${image}" >/dev/null

# Keep the old container until the new one is running, so a failed start can
# be rolled back by renaming it back.
old="${c}-pre-roll"
echo "${c}: stopping ($(j .Config.Image))"
docker stop -t 120 "${c}" >/dev/null
docker rename "${c}" "${old}"
if ! docker run "${args[@]}" "${image}" >/dev/null; then
  echo "${c}: start on ${image} failed, restoring the old container" >&2
  docker rm -f "${c}" >/dev/null 2>&1 || true
  docker rename "${old}" "${c}"
  docker start "${c}" >/dev/null
  exit 1
fi
docker rm "${old}" >/dev/null
echo "${c}: started on ${image}"
