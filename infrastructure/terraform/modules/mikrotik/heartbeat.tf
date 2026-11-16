## ---------------------------------------------------------------------------------------------------------------------
## OFF-SITE HEARTBEAT
##
## The router fetches a healthchecks.io ping URL every minute, and
## healthchecks.io messages Telegram when the pings stop. Next to the cluster's
## own heartbeat (Alertmanager's Watchdog), it tells "power or internet is out"
## (both checks down) apart from "only the cluster is down". The checks are
## infrastructure/terraform/environments/prod/healthchecks.
##
## A scheduler with an inline on_event, no /system/script: there is nothing to
## log. During a WAN outage every fetch fails, so failures are swallowed instead
## of filling the log once a minute; healthchecks.io is the one that notices.
## The certificate is verified against RouterOS's built-in trust store, which
## fetch uses by default (/certificate/settings builtin-trust-store).
##
## The URL carries the project's ping key, so the scheduler body is sensitive
## in the same way as the DDNS script: anyone who can read the router's config
## can ping the check (keep it alive), but nothing else.
## ---------------------------------------------------------------------------------------------------------------------

resource "routeros_system_scheduler" "heartbeat" {
  count = var.heartbeat != null ? 1 : 0

  name     = "heartbeat"
  interval = var.heartbeat.interval
  on_event = ":do { /tool fetch url=\"${var.heartbeat_url}\" check-certificate=yes output=none } on-error={}"

  # test: /tool fetch lives under the test policy.
  policy = ["read", "test"]

  comment = "healthchecks.io heartbeat every ${var.heartbeat.interval} (terraform)"

  lifecycle {
    precondition {
      condition     = var.heartbeat_url != null
      error_message = "heartbeat requires heartbeat_url."
    }
  }
}

## ---------------------------------------------------------------------------------------------------------------------
## VERSION LINE
##
## The API checks of the security platform on homelab-wsl-02 compare the
## RouterOS version with the vendor's newest release (routeros-firmware). mktxp
## reads the version on homelab-wsl only, so this line gives site 2 the version
## while that cluster is down (Design Doc 0004, section 5.5). Once an interval
## the scheduler logs one line with the identity and the version; the logging
## rule in system.tf sends that line, and only that line, to the host of
## site 2. It carries no credential and no setting.
##
## policy: read, for /system identity and /system resource. Nothing else.
## ---------------------------------------------------------------------------------------------------------------------

locals {
  # The prefix that the API checks and the logging rule match. The job
  # (kubernetes/apps/security/api-checks) reads "router=<identity>
  # version=<version>" after it.
  version_line_prefix = "tnwks-routeros-version"
}

resource "routeros_system_scheduler" "version_line" {
  count = var.version_line != null ? 1 : 0

  name     = "version-line"
  interval = var.version_line.interval
  on_event = ":log info (\"${local.version_line_prefix} router=\" . [/system identity get name] . \" version=\" . [/system resource get version])"

  policy = ["read"]

  comment = "security version line every ${var.version_line.interval} (terraform)"
}
