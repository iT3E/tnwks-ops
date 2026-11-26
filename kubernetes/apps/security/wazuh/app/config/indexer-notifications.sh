#!/bin/bash
# Step 1 of the start hook of the indexer (../../ks.yaml): reconcile the one
# Alertmanager channel. A failed hook makes the kubelet restart the container,
# so each request is sent again while the indexer gives no answer or a server
# error, until the deadline of the hook. Each wait, retry and failure writes one
# line to the log of the container (kubectl logs).
set -euo pipefail

# The deadline of both steps of the hook: 300 seconds from its start, the same
# time as the startup probe of the container (10 s x 30). This step runs first,
# so it starts the clock and writes it for step 2 (dashboard/indexer-auth.sh).
deadline=$(( $(date +%s) + 300 ))
echo "$deadline" > /tmp/wazuh-indexer-hook-deadline

# One line with the UTC time, to the log of the container and to stderr. No line
# holds a credential, a certificate or a response body.
log() {
    local line
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) wazuh-indexer start hook, notifications: $*"
    { printf '%s\n' "$line" >> /proc/1/fd/2; } 2>/dev/null || true
    printf '%s\n' "$line" >&2
}

enabled=$(cat /notifications/notifications-enabled)
case "$enabled" in
    true|false) ;;
    *) log "failed, the notification setting is not true or false"; exit 1 ;;
esac

certs=/usr/share/wazuh-indexer/config/certs
indexer=https://wazuh-indexer.security.svc.cluster.local:9200
health="$indexer/_cluster/health?wait_for_status=yellow&timeout=5s"
configs=$indexer/_plugins/_notifications/configs
channel=tnwks-wazuh-alertmanager
response=$(mktemp)
trap 'rm -f "$response"' EXIT

# The seconds that one try may take: at most $1, and not past the deadline.
budget() {
    local left=$(( deadline - $(date +%s) ))
    if (( left > $1 )); then left=$1; fi
    if (( left < 1 )); then left=1; fi
    echo "$left"
}

# Wait 5 seconds before the next try of $1, or fail if the deadline comes first.
pause() {
    if (( $(date +%s) + 5 > deadline )); then
        log "$1: failed ($2), no time is left before the deadline"
        exit 1
    fi
    log "$1: try again in 5 s ($2)"
    sleep 5
}

# Send one request over loopback with the admin certificate. $status is the
# HTTP status, or 000 if no answer came. The body is in $response.
send() {
    local code=0
    status=$(curl --silent --cacert "$certs/root-ca.pem" --cert "$certs/admin.pem" \
        --key "$certs/admin-key.pem" --connect-timeout 2 --max-time "$(budget 10)" \
        --resolve wazuh-indexer.security.svc.cluster.local:9200:127.0.0.1 \
        --output "$response" --write-out '%{http_code}' "$@" 2>/dev/null) || code=$?
    detail="HTTP $status"
    if [ "$code" != 0 ]; then
        status=000
        detail="HTTP 000, curl exit $code"
    fi
}

# Send a request until it gets an answer that is not a server error.
request() {
    local what=$1
    shift
    while true; do
        send "$@"
        case "$status" in
            000|5??) pause "$what" "$detail" ;;
            *) return 0 ;;
        esac
    done
}

# Send a read until it gets HTTP 200.
wait_for() {
    local what=$1
    shift
    while true; do
        send "$@"
        if [ "$status" = 200 ]; then
            return 0
        fi
        pause "$what" "$detail"
    done
}

wait_for "cluster health" "$health"
log "cluster health: yellow or green (HTTP 200)"

request "channel check" "$configs/$channel"
case "$status" in
    200|404) ;;
    *) log "channel check: failed ($detail)"; exit 1 ;;
esac

# This ID is managed here. Refuse to change a differently named or typed object.
if [ "$status" = 200 ]; then
    if ! grep -Fq "\"name\":\"$channel\"" "$response" || ! grep -Fq '"config_type":"webhook"' "$response"; then
        log "channel check: failed, the Alertmanager channel ID is already in use"
        exit 1
    fi
fi

if [ "$enabled" = false ]; then
    if [ "$status" = 200 ]; then
        # A 404 means that an earlier try removed the channel.
        request "channel delete" --request DELETE "$configs/$channel"
        case "$status" in
            2??|404) log "channel delete: done ($detail)" ;;
            *) log "channel delete: failed ($detail)"; exit 1 ;;
        esac
    fi
    exit 0
fi

config='{"name":"tnwks-wazuh-alertmanager","description":"tnwks Wazuh findings","config_type":"webhook","is_enabled":true,"webhook":{"url":"http://alertmanager-wazuh.monitoring.svc.cluster.local:9093/api/v2/alerts","method":"POST","header_params":{"Content-Type":"application/json"}}}'
if [ "$status" = 404 ]; then
    what="channel create"
    request "$what" --json "{\"config_id\":\"$channel\",\"config\":$config}" "$configs"
else
    what="channel update"
    request "$what" --request PUT --json "{\"config\":$config}" "$configs/$channel"
fi
case "$status" in
    2??) log "$what: done ($detail)" ;;
    *) log "$what: failed ($detail)"; exit 1 ;;
esac
