# Vector: the log pipeline

Two parts move logs to Loki. This file says what each one takes in, where it
listens and what it reports about itself.

| Part | Directory | Runs on | What it does |
| --- | --- | --- | --- |
| The aggregator | `aggregator/` (`ks.yaml`) | Each cluster, one pod | Takes in pod logs and the router's syslog, and writes them to the Loki of its cluster. Takes in the audit log of the API server, and writes the security streams |
| The agent | `agent/` (`ks-agent.yaml`) | Each cluster that lists it, one pod on each node | Reads pod logs on its node and sends them to the aggregator. On the WSL clusters, Job pods only (`VECTOR_AGENT_POD_SELECTOR`) |

The config of the aggregator is one file, `aggregator/config/vector.yaml`,
for every cluster. A value that differs between clusters is a variable of
`cluster-settings`.

## The ports of the aggregator

| Port | What it is | Who reaches it |
| --- | --- | --- |
| 6000 | Pod logs from the agent (a `vector` source) | The Service `vector-aggregator`, a LoadBalancer |
| 6001/UDP | The router's syslog | The same Service, through the LAN bridge of the WSL host |
| 8686 | The Vector API | The same Service |
| 6010 | The audit log of the API server (a `vector` source) | The Service `vector-aggregator-audit`, a ClusterIP |
| 9598 | Vector's own metrics | The pod only. Prometheus reads it through the PodMonitor `vector-aggregator` |

The metrics port and the audit port are not on the Service
`vector-aggregator`, because that Service is a LoadBalancer. A new port goes
on it only when a sender outside the cluster needs it.

## The security streams

A security stream is a log that the security platform reads
(`security/README.md`). The aggregator of each cluster writes each one to the
Loki of homelab-wsl-02, which keeps a stream with the label `security_stream`
for 90 days. This section is the contract for a part that sends such a log or
reads it.

| Stream | Stream labels in Loki | What a line is |
| --- | --- | --- |
| The audit log of an API server | `security_stream="k8s-audit"`, `k8s_cluster="<cluster>"` | One audit event, the JSON line as the API server wrote it. The time of the line is the `stageTimestamp` of the event |
| The router log | `security_stream="routeros"`, `k8s_cluster="<cluster>"` | One syslog message as JSON, the same as in the stream `job="routeros"` |

`k8s_cluster` is the name of the cluster that the log comes from
(`CLUSTER_NAME`: `homelab-wsl`, `homelab-wsl-02`), also for a line of
homelab-wsl that rests in the Loki of homelab-wsl-02. A security stream has
these two labels and no other, so each one is two streams in Loki at most.

| What | Address |
| --- | --- |
| The audit source, for the audit reader of the same cluster | `http://vector-aggregator-audit.monitoring.svc.cluster.local:6010` (the Vector protocol, version 2) |
| The Loki of the security streams, from homelab-wsl-02 | `http://loki-gateway.monitoring.svc.cluster.local` |
| The same Loki, from homelab-wsl | `https://security-loki.monitoring.svc.cluster.local`, with the client certificate `security-loki-client` (`../security-loki-proxy/README.md`) |

The aggregator takes the Loki address from `SECURITY_LOKI_ENDPOINT` of
`cluster-settings`. A cluster that does not set it writes to its own Loki.

The two streams behave in two ways when that Loki is away, and each way is on
purpose:

| Stream | Path in `vector.yaml` | When Loki is away |
| --- | --- | --- |
| The audit log | `audit_source`, `audit_time`, `loki_security_audit` | The sink waits and tries again, and each part before it waits too. No line is lost while the audit files on the node last. An acknowledgement goes back to the sender only when Loki took the line |
| The router log | `routeros_syslog`, `loki_security_routeros` | The sink holds 500 events and then drops the newest (`vector_buffer_discarded_events_total`). The sink `loki_routeros` to the Loki of the own cluster does not wait |

Three rules keep the other logs safe:

- **A sink that can wait has a source of its own.** A sink with
  acknowledgements, or with a buffer that blocks, holds its source when its
  store is away. `audit_source` feeds `loki_security_audit` only.
- **A second sink on a source that exists drops when it is full.** It sets
  `buffer.when_full: drop_newest`, as `loki_security_routeros` does.
- **Loki refuses a line that is older than 7 days**, and Vector does not send
  a refused request again. A refusal shows as
  `vector_component_errors_total{component_id="loki_security_audit",error_type="request_failed"}`.

## Metrics

The aggregator has an `internal_metrics` source and a `prometheus_exporter`
sink. The Prometheus of each cluster scrapes them once a minute. Each series
starts with `vector_`, and `component_id` is the name of the source, the
transform or the sink in `vector.yaml`.

| Series | What it counts |
| --- | --- |
| `vector_component_received_events_total` | The events that a source or a sink took in |
| `vector_component_sent_events_total` | The events that a source passed on, or that a sink delivered |
| `vector_component_errors_total` | The errors of a component, by `error_type` and `stage`. For a sink, a request that its store refused for good is `error_type="request_failed"` |
| `vector_component_discarded_events_total` | The events that a component dropped. `intentional="false"` is a loss |
| `vector_buffer_discarded_events_total` | The events that the buffer of a sink dropped because it was full |
| `vector_http_client_responses_total` | The answers that a sink got, by HTTP `status` |

Two settings shape these series:

- **A counter starts at its first event.** Vector makes the series of an
  error when the first error happens, so its first value is 1, and
  `increase()` over a new series is 0. A rule on such a counter must also
  compare it with its value of some minutes before.
- **A metric stays for one hour after its last change**
  (`expire_metrics_secs`). The default of Vector is five minutes, and a
  counter that went away that fast could come back at the same value, which
  no rule can see.

The PodMonitor drops the histogram buckets (26 for each histogram), which
leaves about 170 series for each aggregator, and it drops Vector's `host`
label, which repeats `pod`.
