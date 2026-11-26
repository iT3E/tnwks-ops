#!/bin/bash
# Reconcile only the Security plugin's auth configuration, using its pinned tool.
# Step 2 of the start hook of the indexer (../../../ks.yaml). A failed hook
# makes the kubelet restart the container, so each write is tried again until
# the deadline of the hook. Each wait, retry and failure writes one line to the
# log of the container (kubectl logs).
set -eu
cd /usr/share/wazuh-indexer
certs=$PWD/config/certs
desired=$(mktemp)
response=$(mktemp)
trap 'rm -f "$desired" "$response"' EXIT

# One line with the UTC time, to the log of the container and to stderr. No line
# holds a credential, a certificate or a response body.
log() {
    local line
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) wazuh-indexer start hook, auth: $*"
    { printf '%s\n' "$line" >> /proc/1/fd/2; } 2>/dev/null || true
    printf '%s\n' "$line" >&2
}

# Step 1 (../indexer-notifications.sh) starts the clock of the hook and writes
# its deadline, so this step does not start it again. Run alone, this step has
# 300 seconds.
deadline=$(( $(date +%s) + 300 ))
if [ -f /tmp/wazuh-indexer-hook-deadline ]; then
    deadline=$(cat /tmp/wazuh-indexer-hook-deadline)
    rm -f /tmp/wazuh-indexer-hook-deadline
fi
if ! [[ $deadline =~ ^[0-9]+$ ]]; then
    log "failed, the deadline of the hook is not a number"
    exit 1
fi

# Preserve the packaged settings and Basic authentication used by the services.
enabled=$(cat /dashboard-access/enabled)
if [ "$enabled" = true ]; then
    assistant_users='["admin","kibanaserver","wazuh-manager"]'
    if ! awk '
    /^      multitenancy_enabled: false$/ {
        print "      multitenancy_enabled: true"
        print "      private_tenant_enabled: false"
        tenants++
        next
    }
    /^    authc:$/ {
        print
        while ((getline fragment < "/dashboard-access/indexer-oidc.yaml") > 0) {
            if (fragment !~ /^#/) print fragment
        }
        count++
        next
    }
    { print }
    END { if (count != 1 || tenants != 1) exit 1 }
' config/opensearch-security/config.yml > "$desired"; then
        log "failed, the packaged config.yml does not have one authc and one multitenancy line"
        exit 1
    fi
else
    assistant_users='["*"]'
    cp config/opensearch-security/config.yml "$desired"
fi

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

# The local server has a service DNS certificate. Verification of its CA is
# mandatory, and this socket is confined to indexer loopback.
indexer=https://wazuh-indexer.security.svc.cluster.local:9200
api=$indexer/_plugins/_security/api

# Send one request with the admin certificate. $status is the HTTP status, or
# 000 if no answer came. The body is in $response.
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

apply_assistant_mapping() {
    while true; do
        send --request PATCH --header 'Content-Type: application/json' \
            --data "[{\"op\":\"replace\",\"path\":\"/users\",\"value\":$assistant_users}]" \
            "$api/rolesmapping/wazuh_ai_assistant"
        case "$status" in
            2??) log "assistant mapping: done ($detail)"; return 0 ;;
            000|5??) pause "assistant mapping" "$detail" ;;
            *) log "assistant mapping: failed ($detail)"; exit 1 ;;
        esac
    done
}

apply_auth_config() {
    local code
    while true; do
        code=0
        OPENSEARCH_JAVA_HOME=$PWD/jdk timeout "$(budget 300)" bash plugins/opensearch-security/tools/securityadmin.sh \
            -h 127.0.0.1 -p 9200 -icl -nhnv -cacert "$certs/root-ca.pem" \
            -cert "$certs/admin.pem" -key "$certs/admin-key.pem" -f "$desired" -t config || code=$?
        if [ "$code" = 0 ]; then
            log "auth config: done"
            return 0
        fi
        pause "auth config" "securityadmin.sh exit $code"
    done
}

# Write nothing before the cluster is yellow and the Security API answers.
wait_for "cluster health" "$indexer/_cluster/health?wait_for_status=yellow&timeout=5s"
log "cluster health: yellow or green (HTTP 200)"
wait_for "security API" "$api/securityconfig"

# Limit assistant permissions before admitting Cognito. When withdrawing
# access, remove the authentication domain before restoring the mapping.
if [ "$enabled" = true ]; then
    apply_assistant_mapping
    apply_auth_config
else
    apply_auth_config
    apply_assistant_mapping
fi
