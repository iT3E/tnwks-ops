"""AI (L2): Is each part of the agent platform there and ready, do its model calls, checks, tool calls and backups work, and did a limit or a security signal fire?

The declared parts, checks, result lines and backups are in observability/inventory.py
(AI_*); docs/apps/agent-platform.md describes the platform. Step 1 of the AI board
is the data of today; step 2 adds the series of the model gateway (calls, failed
calls, the time of a call and tokens, by key; docs/apps/litellm.md, "Metrics"),
and step 3 the metrics of the personal agents (messages, turns, replies, stuck
sessions and failovers; docs/apps/agent-platform.md, "Metrics").
No money: the plan is not paid by the call.
"""

from __future__ import annotations

import yaml

from ..design import NO_ALERTS, NO_EVENTS
from ..model import Board, p
from ..queries import ALERT_SCOPE, ROOT, SCOPE, metric, selector, worst

# The rule files of the AI alerts. Each alert links to this board.
ALERT_FILES = (
    "kubernetes/apps/ai/agents/monitoring/app/prometheusrule.yaml",
    "kubernetes/apps/ai/agents/monitoring/app/heartbeat-prometheusrule.yaml",
    "kubernetes/apps/ai/litellm/app/prometheusrule.yaml",
)


def alert_names() -> list[str]:
    return sorted(rule["alert"] for path in ALERT_FILES
                  for doc in yaml.safe_load_all((ROOT / path).read_text()) if doc
                  for group in doc["spec"]["groups"] for rule in group["rules"] if "alert" in rule)


def by_trust_domain(expr: str, label: str = "pod_namespace", pattern: str = "agents-(.+)") -> str:
    return f'label_replace({expr}, "trust_domain", "$1", "{label}", "{pattern}")'


def mcp(window: str, extra: str = "") -> str:
    """The request series of the MCP gateway in a window, less the GET with 405 that
    each start of a client makes (docs/apps/mcp-gateway.md, "Logs and metrics").
    Board queries only: no rule reads these series (tests/test_mcp_gateway.py)."""
    scope = SCOPE + ("," + extra if extra else "")
    return (f'increase(agentgateway_requests_total{{{scope}}}[{window}])'
            f' unless increase(agentgateway_requests_total{{{scope},method="GET",status="405"}}[{window}])')


def mcp_rate(extra: str = "") -> str:
    scope = SCOPE + ("," + extra if extra else "")
    return (f'rate(agentgateway_requests_total{{{scope}}}[$__rate_interval])'
            f' unless rate(agentgateway_requests_total{{{scope},method="GET",status="405"}}[$__rate_interval])')


PARTS = metric("homelab:ai_part_state")
SCHEDULES = metric("homelab:ai_schedule_state")
RESULTS = metric("homelab:ai_result_state")
BACKUPS = metric("homelab:ai_backup_state")
RESULT = "homelab_agent_platform_result_timestamp_seconds"
RUN_JOBS = 'namespace="agents-workflows",job_name=~"run-.+"'
RUN_FAILED = ',reason!=""'
FAILED = 'status=~"[45].."'
BRIDGE = "homelab_model_bridge_engine_error_timestamp_seconds"
LIMIT = 'kind="rate_limit"'
INSTRUCTION_FILE = 'check="instruction-file"'
# The use of the Claude plan, from the lines of the model bridge (observability/rules.py,
# plan_records; docs/apps/agent-platform.md, "Plan use").
PLAN_USED = "homelab:ai_plan_utilization_ratio"
PLAN_RESET = "homelab:ai_plan_reset_timestamp_seconds"
PLAN_STATE = "homelab:ai_plan_state"
PLAN_SEEN = "homelab:ai_plan_observed_timestamp_seconds"
FIVE_HOUR = 'window="five_hour"'
WEEK = 'window="seven_day"'
PLAN_WORDS = (("0", "Not given"), ("1", "Allowed"), ("2", "Near the limit"), ("3", "At the limit"))
NO_READING = ("Unknown: no reading. No model call through the bridge in the last 8 days, the window "
              "reset after the last call, or the vendor did not give the value")
NO_CALLS = "None: no model call through the bridge in the range"
BRIDGE_TOKENS = "homelab_model_bridge_tokens_total"
DENIALS = "apiserver_validating_admission_policy_check_total"
MOVER_DENY = 'policy="agents-backup-mover-configmaps",enforcement_action="deny"'
# The series of the model gateway (kubernetes/apps/ai/litellm/app/configmap.yaml,
# the keep list). The key of the metrics scrape makes refused calls each day on
# purpose (the CronJob litellm-key-check), so the call panels leave it out.
CALLS = "litellm_proxy_total_requests_metric_total"
FAILED_CALLS = "litellm_proxy_failed_requests_metric_total"
CALL_TIME = "litellm_request_total_latency_metric_bucket"
TOKENS_IN = "litellm_input_tokens_metric_total"
TOKENS_OUT = "litellm_output_tokens_metric_total"
CALLERS = 'api_key_alias!="metrics-scrape"'


# The metrics of the product (step 3; docs/apps/agent-platform.md, "Metrics"):
# Alloy keeps 8 instruments and their labels, and Prometheus takes them on its
# OTLP path. job is the service name of the agent (openclaw-agents-<domain>), or
# undeclared.
RECEIVED = "openclaw_message_received_total"
PROCESSED = "openclaw_message_processed_total"
DELIVERIES = "openclaw_message_delivery_started_total"
DELIVERY = "openclaw_message_delivery_duration_ms_milliseconds"
TURNS = "openclaw_session_turn_created_total"
STUCK = "openclaw_session_stuck_total"
FAILOVERS = "openclaw_model_failover_total"
ERROR = 'openclaw_outcome="error"'


def product(name: str, window: str, by: str, extra: str = "") -> str:
    """The increase of a counter of the product in a window, by trust domain and labels."""
    expr = by_trust_domain(f"increase({selector(name, extra)}[{window}])", "job", "(?:openclaw-agents-)?(.+)")
    return f"sum by (k8s_cluster, trust_domain{', ' + by if by else ''}) ({expr})"


def product_rate(name: str, by: str = "", extra: str = "") -> str:
    expr = by_trust_domain(f"rate({selector(name, extra)}[$__rate_interval])", "job", "(?:openclaw-agents-)?(.+)")
    return f"sum by (k8s_cluster, trust_domain{', ' + by if by else ''}) ({expr})"


def calls(name: str, window: str, by: str) -> str:
    """The increase of a counter of the model gateway in a window, by labels."""
    return f"sum by ({by}) (increase({selector(name, CALLERS)}[{window}]))"


def call_rate(name: str, by: str) -> str:
    return f"sum by ({by}) (rate({selector(name, CALLERS)}[$__rate_interval]))"


def tokens(window: str, by: str) -> str:
    return f"{calls(TOKENS_IN, window, by)} + {calls(TOKENS_OUT, window, by)}"


def bridge_tokens(window: str) -> str:
    """The increase of the token counter of the bridge in a window; its label domain is the trust domain."""
    return by_trust_domain(f"increase({selector(BRIDGE_TOKENS)}[{window}])", "domain", "(.+)")


def unanswered() -> str:
    """The messages of each agent with no end of their turn yet: the messages in less the messages
    that ended (completed, skipped or error), by trust domain and channel (AgentTurnNotEnded)."""
    by = "k8s_cluster, job, openclaw_channel"
    got = f"sum by ({by}) ({selector(RECEIVED)})"
    ended = f"sum by ({by}) ({selector(PROCESSED)})"
    gap = by_trust_domain(f"({got} - ({ended} or {got} * 0))", "job", "(?:openclaw-agents-)?(.+)")
    return f"sum by (k8s_cluster, trust_domain, openclaw_channel) ({gap})"


def events(series: str, label: str = "pod_namespace", pattern: str = "agents-(.+)") -> str:
    """The newest event time of each series in the selected range, by trust domain."""
    return by_trust_domain(f"max_over_time({series}[$__range])", label, pattern)


def board() -> Board:
    firing = alerts_firing()
    return Board("tnwks-ai", "AI", "ai", "l2",
              "Is each part of the agent platform there and ready, do its model calls, checks, tool calls and "
              "backups work, and did a limit or a security signal fire?", {
            "Parts": [
                p("Parts", worst(PARTS), "state",
                  "The worst state of the declared AI parts: Failed, then Degraded, then Unknown, then Healthy. Standby and Not applicable do not count.",
                  "stat"),
                p("Checks", worst(SCHEDULES, RESULTS), "state",
                  "The worst state of the scheduled checks and of the result lines of the agent pods.", "stat"),
                p("Backups", worst(BACKUPS), "state",
                  "The worst state of the snapshot policies of the state volumes and the vault shares.", "stat"),
                p("Part state", PARTS, "state",
                  "Each part that observability/inventory.py declares, for each trust domain and site, from kube-state-metrics: Healthy when each wanted replica is ready, Degraded when some are, Failed when none is, Standby at zero replicas. A declared part with no workload stays a row, as Unknown. A part that a site does not run by design is Not applicable there.",
                  "table"),
                p("AI alerts firing", firing, "short",
                  "The alerts of the agent platform, the model gateway and the heartbeat that fire now, by the site whose Prometheus raised them.",
                  "table", "tnwks-alerts", empty=NO_ALERTS, column=None, role="status"),
            ],
            "Model calls": [
                p("Model calls by key", calls(CALLS, "$__range", "k8s_cluster, api_key_alias, requested_model, status_code"),
                  "short",
                  "Calls to the model gateway in the selected range, by the key of the caller, the name it asked for and the status. The key of a trust domain has the name of its domain (agents-main); a key that is not declared in the scrape reads undeclared, and None is a call with no valid key. The daily refused calls of the key of the metrics scrape are left out (Checks tab, litellm-key-check).",
                  "table", column="Calls"),
                p("Failed model calls by status", f'{calls(FAILED_CALLS, "$__range", "k8s_cluster, api_key_alias, requested_model, exception_status")} > 0',
                  "short",
                  "Calls that failed in the selected range, by key, name and status: 429 is the limit of the plan, 401 or 403 a key or a model list, 5xx the bridge, Bedrock or the vendor. ModelGatewayCallsFailing fires when more than half of the calls of a trust domain fail for 20 minutes.",
                  "table", column="Failed calls", empty=NO_EVENTS),
                p("Model calls", call_rate(CALLS, "k8s_cluster, api_key_alias"), "reqps",
                  "Calls to the model gateway each second, by the key of the caller."),
                p("Share of model calls that failed",
                  f'{call_rate(FAILED_CALLS, "k8s_cluster, api_key_alias")} / {call_rate(CALLS, "k8s_cluster, api_key_alias")}',
                  "percentunit",
                  "The failed calls as a share of all calls, by key. A gap is a time with no call."),
                p("Time of a model call (p95)",
                  f'histogram_quantile(0.95, {call_rate(CALL_TIME, "k8s_cluster, api_key_alias, le")})', "s",
                  "The 95th percentile of the time of a call that passed, from the request to the end of the answer, by key. A turn of an agent with tools makes many calls."),
                p("Tokens per day", tokens("1d", "k8s_cluster, api_key_alias, requested_model"), "short",
                  "The input and output tokens of the 24 hours before each point, by key and the name it asked for. The plan is one budget that only the owner sees, so no panel shows a share of the limit or money."),
                p("Tokens in the range", tokens("$__range", "k8s_cluster, api_key_alias, requested_model, api_provider"),
                  "short",
                  "The input and output tokens in the selected range, by key, name and provider.",
                  "table", column="Tokens"),
            ],
            "Messages": [
                p("Messages by channel", product(RECEIVED, "$__range", "openclaw_channel"), "short",
                  "The messages that reached each personal agent in the selected range, by trust domain and channel, as the agent counts them. A trust domain of undeclared is a sender with a service name that is not one of the three.",
                  "table", column="Messages"),
                p("Turns by outcome", product(PROCESSED, "$__range", "openclaw_channel, openclaw_outcome"), "short",
                  "The messages that each agent finished in the selected range, by outcome: completed, skipped or error. A value that is not declared reads other.",
                  "table", column="Messages"),
                p("Messages in", product_rate(RECEIVED), "reqps",
                  "Messages in each second, by trust domain."),
                p("Turns that ended in an error", product_rate(PROCESSED, extra=ERROR), "reqps",
                  "Messages that an agent finished with an error, each second, by trust domain.", empty=NO_EVENTS),
                p("Replies sent", product(DELIVERIES, "$__range", "openclaw_channel, openclaw_delivery_kind"), "short",
                  "The replies that each agent started to send in the selected range, by channel and kind (text or media).",
                  "table", column="Replies"),
                p("Replies that failed",
                  f'{product(DELIVERY + "_count", "$__range", "openclaw_channel, openclaw_delivery_kind, openclaw_errorCategory", ERROR)} > 0',
                  "short",
                  "The replies that the channel did not take in the selected range, by channel, kind and the class of the error. The text of the error stays out.",
                  "table", column="Failed replies", empty=NO_EVENTS),
                p("Time to send a reply (p95)",
                  f'histogram_quantile(0.95, {product_rate(DELIVERY + "_bucket", "le")})', "ms",
                  "The 95th percentile of the time to hand a reply to its channel, by trust domain. A gap is a time with no reply."),
                p("Turns by trigger", product(TURNS, "$__range", "openclaw_trigger"), "short",
                  "The turns that each agent started in the selected range, by what started them: a message of a user, the heartbeat, or an event such as a scheduled job.",
                  "table", column="Turns"),
                p("Stuck sessions", f'{product(STUCK, "$__range", "openclaw_state")} > 0', "short",
                  "The times in the selected range that an agent found a session that did not end, by its state.",
                  "table", column="Times", empty=NO_EVENTS),
                p("Messages with no end", f"{unanswered()} > 0", "short",
                  "The messages that each agent took and has not ended its turn for, by channel: a turn that runs now, or one that hangs (a model call, a tool, a streamed part). A final reply that hangs after the turn ended is not counted here. AgentTurnNotEnded fires when one stays for 30 minutes. A restart of the agent pod starts the count again.",
                  "table", column="Messages", empty=NO_EVENTS),
                p("Model failovers", f'{product(FAILOVERS, "$__range", "openclaw_provider, openclaw_model, openclaw_failover_to_model")} > 0',
                  "short",
                  "The times in the selected range that an agent moved a turn to a second model. Each trust domain has one model name, so each row here is against the rule of one model for each domain.",
                  "table", column="Failovers", empty=NO_EVENTS),
            ],
            "Tool calls": [
                p("MCP requests by route", f'sum by (k8s_cluster, route, method, status) ({mcp("$__range")})', "short",
                  "Requests to the MCP gateway in the selected range, by route, HTTP method and status. The GET with 405 that each start of a client makes is left out: the gateway offers no stream on a stateless route, and that is not a failure. A route is the tools of one trust domain (default/finance, default/finance-vault).",
                  "table", column="Requests"),
                p("MCP failed requests", f"sum by (k8s_cluster, route, status) ({mcp_rate(FAILED)})", "reqps",
                  "Requests to the MCP gateway with a 4xx or 5xx status, by route, less the GET with 405 of a client start. A 401 is a client with no key or a wrong key, a 400 a tool that the route does not allow.",
                  empty=NO_EVENTS),
            ],
            "Checks": [
                p("Scheduled check state", SCHEDULES, "state",
                  "Each declared CronJob of the platform: Healthy when its last good run is within its window (26 hours for a daily check, 35 minutes for the workflow jobs), Degraded when it is older, Failed when the newest run failed after the last good one, Standby when the CronJob is suspended, Unknown with no evidence.",
                  "table"),
                p("Time since the last good run",
                  f'max by (k8s_cluster, trust_domain, namespace, cronjob) ({selector("homelab_ai_schedule_objective_seconds")}) * 0'
                  f' + on (k8s_cluster, namespace, cronjob) group_left () (time() - max by (k8s_cluster, namespace, cronjob) ({selector("kube_cronjob_status_last_successful_time")}))',
                  "s", "The time since the last good run of each declared CronJob, from kube-state-metrics.", "table"),
                p("Result line state", RESULTS, "state",
                  "The newest `<check> result=` line of each declared check of an agent pod in its window (26 hours for the instruction check and the daily archive, 15 days for the memory index, which runs at each start of the pod, 3 hours for the sync of the automations, 30 minutes for the drift of the app of the reviewer): Healthy when it is a good result, Failed when it is not, Unknown when no line came.",
                  "table"),
                p("Workflow run Jobs", f'label_replace(count by (k8s_cluster) ({selector("kube_job_status_succeeded", RUN_JOBS)} > 0), "result", "succeeded", "", "")'
                  f' or label_replace(count by (k8s_cluster) ({selector("kube_job_status_failed", RUN_JOBS + RUN_FAILED)} > 0), "result", "failed", "", "")',
                  "short", "The run Jobs of the workflow agents that Kubernetes still keeps, by result. A failed run alerts for 2 hours (AgentWorkflowRunFailed).",
                  "table", column="Jobs"),
            ],
            "Backups": [
                p("Backup state", BACKUPS, "state",
                  "Each declared snapshot policy of a trust domain: Healthy when its last good snapshot is within the recovery point objective (30 hours), Failed when it is older, Unknown with no evidence from Kopiur.",
                  "table"),
                p("Backup age", metric("homelab:ai_backup_age_seconds"), "s",
                  "The time since the last good snapshot of each declared policy, from Kopiur.", "table"),
                p("Last restore test", f'max by (k8s_cluster, trust_domain, check, result) ({selector("homelab:ai_restore_result_timestamp_seconds")}) * 1000', "dateTimeAsIso",
                  "The time of the newest result line of each restore test, by result. A restore test runs by hand, and its line lives 5 minutes as a series, so a recording rule keeps its time for the local history of Prometheus (15 days). An older test is not shown here, and no test reads Unknown.",
                  "table"),
            ],
            "Limits and security": [
                p("Five hour window used", f"max({selector(PLAN_USED, FIVE_HOUR)})", "percentunit",
                  "How much of the five hour window of the Claude plan is used, from the newest reading of the model "
                  "bridge. At 100% the calls of each trust domain stop until the window resets. The bridge reads the "
                  "plan on each answer, and logs a reading when the use moves by a whole percent, so the share can "
                  "be up to one percent low.", "stat", empty=NO_READING),
                p("Five hour window resets in", f"max({selector(PLAN_RESET, FIVE_HOUR)}) - time()", "s",
                  "The time until the five hour window starts again at 0%. The window starts with the first call "
                  "after the last reset.", "stat", empty=NO_READING),
                p("Week used", f"max({selector(PLAN_USED, WEEK)})", "percentunit",
                  "How much of the weekly window of the Claude plan is used, from the newest reading of the model "
                  "bridge. At 100% the calls of each trust domain stop until the week resets.",
                  "stat", empty=NO_READING),
                p("Week resets in", f"max({selector(PLAN_RESET, WEEK)}) - time()", "s",
                  "The time until the weekly window starts again at 0%.", "stat", empty=NO_READING),
                p("State of each plan window", f"max by (k8s_cluster, window) ({selector(PLAN_STATE)})", "state",
                  "One row for each window of the plan that the bridge read: Allowed, Near the limit (the warning "
                  "of the vendor, which also sends the alert ModelPlanNearLimit to the chat), or At the limit (calls "
                  "stop). Not given: the vendor sent the use of the window but no status, as it does for a window "
                  "that does not limit now. A row goes when its window resets. "
                  "five_hour is the five hour window, seven_day the week; seven_day_opus and seven_day_sonnet are "
                  "week windows of one model family, and the overage rows are extra use, not a limit.",
                  "table", empty=NO_READING, states=PLAN_WORDS),
                p("Plan use over time", f"max by (k8s_cluster, window) ({selector(PLAN_USED)})", "percentunit",
                  "The share used of each window of the plan, from the newest reading at each time. A line drops to "
                  "nothing when its window resets, and comes back with the next call.",
                  empty="Unknown: no reading in the range. No model call through the bridge"),
                p("Age of the last reading", f"time() - max by (k8s_cluster, window) ({selector(PLAN_SEEN)})", "s",
                  "The time since the bridge last logged a reading of each window. The bridge logs one when the use, "
                  "the status or the reset time moves, and at most every 30 minutes while calls go on, so an old "
                  "reading with no calls since is still true.",
                  "table", column="Age", empty=NO_READING),
                p("Model tokens by trust domain",
                  f"sum by (k8s_cluster, trust_domain, token) ({bridge_tokens('$__rate_interval')})",
                  "short",
                  "The tokens of the calls through the bridge, by trust domain and kind: input, output, cache_read "
                  "(input read again from the cache, the cheapest kind) and cache_write. All trust domains use the "
                  "same plan, so this says which domain uses it.",
                  empty=NO_CALLS),
                p("Model tokens in the range",
                  f"sum by (k8s_cluster, trust_domain, token) ({bridge_tokens('$__range')}) > 0",
                  "short",
                  "The tokens of the calls through the bridge in the selected range, by trust domain and kind. A "
                  "restart of the log agent starts the count again; the increase still counts across it.",
                  "table", column="Tokens", empty=NO_CALLS),
                p("Plan limit reached",
                  f"max by (k8s_cluster, trust_domain) ({events(selector(BRIDGE, LIMIT), 'domain', '(.+)')}) * 1000 > $__from",
                  "dateTimeAsIso",
                  "The newest time in the range that the model bridge logged a call of a trust domain stopped by the limit of the Claude plan. The calls of that domain fail until the limit resets.",
                  "table", empty=NO_EVENTS),
                p("Instruction file changes",
                  f"max by (k8s_cluster, trust_domain, result) ({events(selector(RESULT, INSTRUCTION_FILE))}) * 1000 > $__from",
                  "dateTimeAsIso",
                  "The newest time in the range that the daily check of an agent pod found an instruction file new, changed or gone. The name of the file stays in Loki.",
                  "table", empty=NO_EVENTS),
                p("Mover denials", f"max by (k8s_cluster, policy) (max_over_time({selector(DENIALS, MOVER_DENY)}[$__range])) > 0",
                  "short",
                  "Denials by the admission rule that keeps the backup mover from the ConfigMaps of a trust domain, counted since the start of the API server. The series appears with the first denial.",
                  "table", column="Denials", empty=NO_EVENTS),
            ],
        }, "AI", sections=True)


def alerts_firing() -> str:
    names = "|".join(alert_names())
    selector_ = f'ALERTS{{{ALERT_SCOPE},alertstate="firing",alertname=~"{names}"}}'
    return ('label_replace(max by (cluster, alertname, severity) '
            f'({selector_}), "k8s_cluster", "$1", "cluster", "(.+)")')
