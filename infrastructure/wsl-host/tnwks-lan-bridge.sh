#!/bin/bash
# tnwks-lan-bridge — bridge LAN-facing ports on the WSL host's eth0 to the
# in-cluster MetalLB VIPs. Windows-side netsh portproxy then forwards from
# the LAN to 127.0.0.1 (which WSL2 mirrors into eth0:port automatically).
#
# Chain:
#   LAN device → VyOS DNS resolves *.internal.tnwks.us → <windows-ip>
#   <windows-ip>:80/443/1883/53-UDP
#     → netsh portproxy → 127.0.0.1:port (WSL2 localhost forwarding)
#     → socat (this script) → MetalLB IP

set -euo pipefail

INTERNAL_INGRESS_IP="${INTERNAL_INGRESS_IP:-10.5.0.200}"
K8S_GATEWAY_IP="${K8S_GATEWAY_IP:-10.5.0.201}"
MQTT_IP="${MQTT_IP:-10.5.0.203}"

# socat must exist on the WSL host. Apt-get on Ubuntu is fine.
command -v socat >/dev/null || { echo "socat missing — sudo apt-get install -y socat"; exit 1; }

run() {
  local listen="$1" target="$2" proto="${3:-TCP}"
  if [[ "$proto" == "UDP" ]]; then
    exec socat -d "UDP-LISTEN:${listen},reuseaddr,fork,bind=0.0.0.0" "UDP:${target}"
  else
    exec socat -d "TCP-LISTEN:${listen},reuseaddr,fork,bind=0.0.0.0" "TCP:${target}"
  fi
}

case "${1:-}" in
  http)   run 80   "${INTERNAL_INGRESS_IP}:80"   TCP ;;
  https)  run 443  "${INTERNAL_INGRESS_IP}:443"  TCP ;;
  mqtt)   run 1883 "${MQTT_IP}:1883"             TCP ;;
  dns)    run 53   "${K8S_GATEWAY_IP}:53"        UDP ;;
  *)
    echo "usage: $0 {http|https|mqtt|dns}" >&2
    exit 2
    ;;
esac
