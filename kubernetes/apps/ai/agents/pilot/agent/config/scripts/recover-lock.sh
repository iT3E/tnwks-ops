#!/bin/sh
# The lock step of the pod: it recovers the Gateway state lock that an
# earlier pod of this Deployment left when it did not stop cleanly (a SIGKILL
# after the grace period, a node or host restart). From OpenClaw 2026.11.10
# the product never takes over a lock whose holder ran in another PID
# namespace, and each pod has its own, so doctor and the gateway of the next
# pod would wait on that lock at each start and fail.
#
# A lock of another pod is a dead lock here: the Deployment is Recreate, on
# one pinned node, with a ReadWriteOnce claim, so the earlier pod has gone
# before this one starts, and this step runs before any OpenClaw process of
# this pod. The step still recovers only when each holder that the product
# names is of another pod (its host is not this pod's name) and was last
# renewed STALE_SECONDS ago or more. Then it confirms the fingerprint of that
# inspection: the product refuses the confirmation if the lock changed since.
# A holder of this pod (a container restart in the same pod, same PID
# namespace) is left to the product, which can check it.
#
# Run it in an init container before doctor, with the env of the gateway. It
# needs no secret and no network. It prints one line, and exits 1 on a
# failure:
#   recover-lock result=none|same-pod|recovered holders=<n>
#   recover-lock result=failed reason=<reason>
set -eu

STALE_SECONDS="${STALE_SECONDS:-60}"
host="$(hostname)"

fail() {
  echo "recover-lock result=failed reason=$1"
  exit 1
}

inspect() {
  node /app/openclaw.mjs gateway recover-lock --json 2>/dev/null || true
}

# "<decision> <holders> <youngest age> <fingerprint>" of an inspection.
# shellcheck disable=SC2016 # the ${...} is JavaScript.
decide() {
  HOST="$host" node -e '
    let s = "";
    process.stdin.on("data", d => (s += d)).on("end", () => {
      let j;
      try { j = JSON.parse(s.slice(s.indexOf("{"))); } catch { console.log("unreadable 0 0 -"); return; }
      if (!j.present) { console.log("none 0 0 -"); return; }
      const holders = j.holders ?? [];
      const re = /holder \d+ at ([^\s/]+)\/pid-ns \d+, last renewed (\d+) s ago/;
      let youngest = Infinity;
      for (const h of holders) {
        const m = re.exec(h);
        if (!m) { console.log(`unparsed ${holders.length} 0 -`); return; }
        if (m[1] === process.env.HOST) { console.log(`same-pod ${holders.length} 0 -`); return; }
        youngest = Math.min(youngest, Number(m[2]));
      }
      if (!holders.length || !/^[0-9a-f]{64}$/.test(j.confirmation ?? "")) {
        console.log(`unparsed ${holders.length} 0 -`); return;
      }
      console.log(`other-pod ${holders.length} ${youngest} ${j.confirmation}`);
    });'
}

# shellcheck disable=SC2046 # four words, split on purpose
set -- $(inspect | decide)
decision="$1" holders="$2" age="$3" fingerprint="$4"
if [ "$decision" = other-pod ] && [ "$age" -lt "$STALE_SECONDS" ]; then
  # Renewed a moment ago: wait until it is stale, and look again.
  sleep $((STALE_SECONDS - age))
  # shellcheck disable=SC2046 # four words, split on purpose
  set -- $(inspect | decide)
  decision="$1" holders="$2" age="$3" fingerprint="$4"
fi

case "$decision" in
  none | same-pod)
    echo "recover-lock result=$decision holders=$holders"
    exit 0
    ;;
  other-pod) ;;
  *) fail "$decision" ;;
esac
[ "$age" -ge "$STALE_SECONDS" ] || fail "renewed-${age}s-ago"

node /app/openclaw.mjs gateway recover-lock --confirm "$fingerprint" --json > /dev/null 2>&1 \
  || fail "confirm-refused"
[ "$(inspect | decide | cut -d' ' -f1)" = none ] || fail "still-held"
echo "recover-lock result=recovered holders=$holders"
