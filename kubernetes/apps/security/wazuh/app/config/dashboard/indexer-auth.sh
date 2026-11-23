#!/bin/bash
# Reconcile only the Security plugin's auth configuration, using its pinned tool.
set -eu
cd /usr/share/wazuh-indexer
certs=$PWD/config/certs
desired=$(mktemp)
trap 'rm -f "$desired"' EXIT

# Preserve the packaged settings and Basic authentication used by the services.
enabled=$(cat /dashboard-access/enabled)
if [ "$enabled" = true ]; then
    assistant_users='["admin","kibanaserver","wazuh-manager"]'
    awk '
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
' config/opensearch-security/config.yml > "$desired"
else
    assistant_users='["*"]'
    cp config/opensearch-security/config.yml "$desired"
fi

# The local server has a service DNS certificate. Verification of its CA is
# mandatory, and this socket is confined to indexer loopback.
deadline=$((SECONDS + 300))
api="https://wazuh-indexer.security.svc.cluster.local:9200/_plugins/_security/api"
security_api() {
    curl --silent --show-error --fail --cacert "$certs/root-ca.pem" --cert "$certs/admin.pem" \
        --key "$certs/admin-key.pem" --connect-timeout 2 --max-time 5 \
        --resolve wazuh-indexer.security.svc.cluster.local:9200:127.0.0.1 "$@"
}
until security_api --output /dev/null "$api/securityconfig" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
        echo "Indexer security API did not become ready" >&2
        exit 1
    fi
    sleep 5
done

apply_assistant_mapping() {
    security_api --output /dev/null --request PATCH --header 'Content-Type: application/json' \
        --data "[{\"op\":\"replace\",\"path\":\"/users\",\"value\":$assistant_users}]" \
        "$api/rolesmapping/wazuh_ai_assistant"
}

apply_auth_config() {
    OPENSEARCH_JAVA_HOME=$PWD/jdk bash plugins/opensearch-security/tools/securityadmin.sh \
        -h 127.0.0.1 -p 9200 -icl -nhnv -cacert "$certs/root-ca.pem" \
        -cert "$certs/admin.pem" -key "$certs/admin-key.pem" -f "$desired" -t config
}

# Limit assistant permissions before admitting Cognito. When withdrawing
# access, remove the authentication domain before restoring the mapping.
if [ "$enabled" = true ]; then
    apply_assistant_mapping
    apply_auth_config
else
    apply_auth_config
    apply_assistant_mapping
fi
