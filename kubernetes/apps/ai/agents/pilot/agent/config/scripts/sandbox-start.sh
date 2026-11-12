#!/bin/sh
# The sandbox-start container of the agent pod. It starts each sandbox of the
# product that is in phase Stopped, so the next command of the agent finds it
# ready.
#
# When the sandbox gateway starts again (a node restart, a new release), it
# stops the sandboxes it had, and only a start call brings one back. The
# product makes a sandbox when none exists, but it does not start a stopped
# one.
#
# At the start it waits for the sandbox gateway, at most WAIT seconds, as the
# gateway can come up after this pod. Then it checks every EVERY seconds, so
# a restart of the gateway alone is covered too. It starts only the
# sandboxes of the product (names "oc-") in phase Stopped, with the CLI of
# the image and the client certificate that the prepare init container
# copies. One log line for each start:
# "sandbox-start name=<name> result=ok|failed". A sandbox stopped by hand
# starts again within EVERY seconds.
set -u

ENDPOINT=https://openshell.agents-pilot.svc.cluster.local:8080
WAIT=300
EVERY=60

list() {
  timeout 30 openshell --gateway-endpoint "$ENDPOINT" sandbox list -o json 2>/dev/null
}

stopped() {
  # The names of the product's sandboxes in phase Stopped, one per line.
  list | node -e '
    const out = JSON.parse(require("fs").readFileSync(0, "utf8"));
    for (const s of out.sandboxes || []) {
      if (s.phase === "Stopped" && /^oc-[a-z0-9]+$/.test(s.name)) console.log(s.name);
    }' 2>/dev/null
}

deadline=$(($(date +%s) + WAIT))
until list > /dev/null || [ "$(date +%s)" -ge "$deadline" ]; do
  sleep 5
done

while true; do
  # A name is a DNS label, so the word split is safe.
  for name in $(stopped); do
    # The start waits until the sandbox is ready (at most 300 seconds).
    if timeout 360 openshell --gateway-endpoint "$ENDPOINT" sandbox start "$name" > /dev/null 2>&1; then
      echo "sandbox-start name=$name result=ok"
    else
      echo "sandbox-start name=$name result=failed"
    fi
  done
  sleep "$EVERY"
done
