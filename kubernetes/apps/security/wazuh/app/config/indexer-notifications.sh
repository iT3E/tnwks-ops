#!/bin/bash
set -euo pipefail

enabled=$(cat /notifications/notifications-enabled)
case "$enabled" in
    true|false) ;;
    *) echo "Invalid Wazuh notification setting" >&2; exit 1 ;;
esac

certs=/usr/share/wazuh-indexer/config/certs
configs=https://wazuh-indexer.security.svc.cluster.local:9200/_plugins/_notifications/configs
channel=tnwks-wazuh-alertmanager
response=$(mktemp)
trap 'rm -f "$response"' EXIT
security_api() {
    curl --silent --show-error --cacert "$certs/root-ca.pem" --cert "$certs/admin.pem" \
        --key "$certs/admin-key.pem" --connect-timeout 2 --max-time 10 \
        --resolve wazuh-indexer.security.svc.cluster.local:9200:127.0.0.1 "$@"
}

deadline=$((SECONDS + 300))
while true; do
    status=$(security_api -o "$response" -w '%{http_code}' "$configs/$channel" 2>/dev/null) || status=000
    case "$status" in
        200|404) break ;;
        000|503) ;;
        *) echo "Could not check the Alertmanager channel (HTTP $status)" >&2; exit 1 ;;
    esac
    if (( SECONDS >= deadline )); then
        echo "Indexer Notifications API did not become ready" >&2
        exit 1
    fi
    sleep 5
done

# This ID is managed here. Refuse to change a differently named or typed object.
if [ "$status" = 200 ]; then
    if ! grep -Fq "\"name\":\"$channel\"" "$response" || ! grep -Fq '"config_type":"webhook"' "$response"; then
        echo "The Alertmanager channel ID is already in use" >&2
        exit 1
    fi
fi

if [ "$enabled" = false ]; then
    if [ "$status" = 200 ]; then
        security_api --fail --output /dev/null --request DELETE "$configs/$channel"
    fi
    exit 0
fi

config='{"name":"tnwks-wazuh-alertmanager","description":"tnwks Wazuh findings","config_type":"webhook","is_enabled":true,"webhook":{"url":"http://alertmanager-wazuh.monitoring.svc.cluster.local:9093/api/v2/alerts","method":"POST","header_params":{"Content-Type":"application/json"}}}'
if [ "$status" = 404 ]; then
    security_api --fail --output /dev/null --json "{\"config_id\":\"$channel\",\"config\":$config}" "$configs"
else
    security_api --fail --output /dev/null --request PUT --json "{\"config\":$config}" "$configs/$channel"
fi
