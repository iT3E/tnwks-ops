#!/bin/sh
# The memory index step: an init container runs it at each start of the agent
# pod, after doctor and before the product. At this release a change of the
# embedding provider or model leaves the index stale: the product only marks
# it, and its CLI refuses to rebuild while the gateway owns the state. Here
# no gateway runs yet, so `memory index` rebuilds a stale index in full, and
# a valid one gets an incremental pass (only changed files).
#
# It never stops the start of the pod: on any failure, or after the time
# limit, search stays as it was until the next start. The CLI's own output
# can hold paths and error text, so it goes nowhere; the step prints one
# line, `memory-index result=ok` or `memory-index result=failed`.
set -u

LIMIT_SECONDS=180

if timeout -k 10 "$LIMIT_SECONDS" node /app/openclaw.mjs memory index --agent main > /dev/null 2>&1; then
  echo "memory-index result=ok"
else
  echo "memory-index result=failed"
fi
exit 0
