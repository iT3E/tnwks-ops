# The path to the Loki of the security streams

The Loki of homelab-wsl-02 keeps each security stream for 90 days
(`kubernetes/clusters/wsl-02/apps/kustomization.yaml`). This directory is
the TLS proxy in front of it, for a sender on the other site.

## The address for a sender

| The sender runs on | Address | What it needs |
| --- | --- | --- |
| homelab-wsl-02 | `http://loki-gateway.monitoring.svc.cluster.local` | Nothing more: the gateway of the Loki of its own cluster |
| homelab-wsl | `https://security-loki.monitoring.svc.cluster.local` | The client certificate and the CA of the Secret `security-loki-client` (namespace `monitoring`), and a rule for its pods in the network policy of the proxy |

Both are the base address of Loki: a sender adds `/loki/api/v1/push`.

## The parts

| Part | Where it is | What it does |
| --- | --- | --- |
| The Service `security-loki` | `../observability-pki/app/security-loki-service.yaml`, on both sites | A global Service of the Cluster Mesh. Its only backend is the proxy on homelab-wsl-02, so the name answers on both sites |
| The proxy | `app/`, on homelab-wsl-02 only | Caddy. It ends TLS, asks for a client certificate of the shared CA, and passes the request to the Loki gateway |
| The certificates | `../observability-pki/app/certificates.yaml`, on both sites | `security-loki-server` for the proxy and `security-loki-client` for a sender. cert-manager makes and renews both from the Issuer `observability-ca` |
| The network policy | `app/networkpolicy.yaml` | The proxy takes connections from the Vector aggregator of the other site only, and it calls the Loki gateway only |

The proxy runs on the node of the security platform (`SECURITY_NODE`), with a
memory limit of 64 MiB (`kubernetes/apps/security/README.md`). It is in
`monitoring`, because the Issuer of the shared CA is in that namespace.

## What the proxy passes on

| Request | Answer |
| --- | --- |
| No client certificate, or one of another CA | The TLS handshake fails |
| `POST /loki/api/v1/push` | Passed to Loki: the push of log lines |
| `GET /` | Passed to the gateway, which answers 200. Vector asks for it as its health check, after `/ready` |
| Each other request | 404. No request through the proxy reads a stream |

## A new sender on homelab-wsl

1. Mount the Secret `security-loki-client` in the sender, and give it
   `tls.crt`, `tls.key` and `ca.crt`. A sender that reads the certificate
   only at its start needs `reloader.stakater.com/auto`, because cert-manager
   renews the certificate each 60 days.
2. Add a rule for its pods to `ingress` in `app/networkpolicy.yaml`.

A certificate lasts 90 days, and cert-manager renews it 30 days before its
end. Reloader then starts the proxy again, which takes some seconds.
