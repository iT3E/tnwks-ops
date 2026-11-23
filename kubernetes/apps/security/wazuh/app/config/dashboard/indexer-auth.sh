#!/bin/bash
# Reconcile only the Security plugin's auth configuration, using its pinned tool.
set -eu
cd /usr/share/wazuh-indexer
certs=$PWD/config/certs
desired=$(mktemp)
trap 'rm -f "$desired"' EXIT

# Preserve the packaged settings and Basic authentication used by the services.
if [ "$(cat /dashboard-access/enabled)" = true ]; then
    awk '
    /^    authc:$/ {
        print
        while ((getline fragment < "/dashboard-access/indexer-oidc.yaml") > 0) {
            if (fragment !~ /^#/) print fragment
        }
        count++
        next
    }
    { print }
    END { if (count != 1) exit 1 }
' config/opensearch-security/config.yml > "$desired"
else
    cp config/opensearch-security/config.yml "$desired"
fi

# The local server has a service DNS certificate. Verification of its CA is
# mandatory, and this socket is confined to indexer loopback.
deadline=$((SECONDS + 300))
until curl --silent --fail --cacert "$certs/root-ca.pem" --cert "$certs/admin.pem" \
    --key "$certs/admin-key.pem" --connect-timeout 2 --max-time 5 \
    --resolve wazuh-indexer.security.svc.cluster.local:9200:127.0.0.1 \
    https://wazuh-indexer.security.svc.cluster.local:9200/_plugins/_security/api/securityconfig \
    --output /dev/null; do
    if (( SECONDS >= deadline )); then
        echo "Indexer security API did not become ready" >&2
        exit 1
    fi
    sleep 5
done

OPENSEARCH_JAVA_HOME=$PWD/jdk bash plugins/opensearch-security/tools/securityadmin.sh \
    -h 127.0.0.1 -p 9200 -icl -nhnv -cacert "$certs/root-ca.pem" \
    -cert "$certs/admin.pem" -key "$certs/admin-key.pem" -f "$desired" -t config
