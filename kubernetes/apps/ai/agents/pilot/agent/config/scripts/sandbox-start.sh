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
#
# The pods of each trust domain run this one script. The address of the
# sandbox gateway of its own trust domain is the key sandbox-gateway-endpoint
# of the same ConfigMap (../kustomization.yaml), next to this file. With no
# address the script logs one line and does nothing. When the gateway does
# not answer, it logs one line, and one more when it answers again:
# "sandbox-start result=failed reason=connect endpoint=<endpoint>" and
# "sandbox-start result=ok reason=connect endpoint=<endpoint>".
set -u

ENDPOINT=$(cat "$(dirname "$0")/sandbox-gateway-endpoint" 2>/dev/null)
WAIT=300
EVERY=60

if [ -z "$ENDPOINT" ]; then
  echo "sandbox-start result=failed reason=no-endpoint"
  # No exit: a restart loop would add nothing, and the pod stays up.
  while true; do sleep 3600; done
fi

list() {
  timeout 30 openshell --gateway-endpoint "$ENDPOINT" sandbox list -o json 2>/dev/null
}

stopped() {
  # The names of the product's sandboxes in phase Stopped, one per line, from
  # the list on stdin.
  node -e '
    const out = JSON.parse(require("fs").readFileSync(0, "utf8"));
    for (const s of out.sandboxes || []) {
      if (s.phase === "Stopped" && /^oc-[a-z0-9]+$/.test(s.name)) console.log(s.name);
    }' 2>/dev/null
}

deadline=$(($(date +%s) + WAIT))
until list > /dev/null || [ "$(date +%s)" -ge "$deadline" ]; do
  sleep 5
done

down=0
while true; do
  if out=$(list); then
    if [ "$down" -eq 1 ]; then
      echo "sandbox-start result=ok reason=connect endpoint=$ENDPOINT"
      down=0
    fi
  else
    if [ "$down" -eq 0 ]; then
      echo "sandbox-start result=failed reason=connect endpoint=$ENDPOINT"
      down=1
    fi
    sleep "$EVERY"
    continue
  fi
  # A name is a DNS label, so the word split is safe.
  for name in $(printf '%s\n' "$out" | stopped); do
    # The start waits until the sandbox is ready (at most 300 seconds).
    if timeout 360 openshell --gateway-endpoint "$ENDPOINT" sandbox start "$name" > /dev/null 2>&1; then
      echo "sandbox-start name=$name result=ok"
    else
      echo "sandbox-start name=$name result=failed"
    fi
  done
  sleep "$EVERY"
done
