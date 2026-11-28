"""The one user of the Control UI of main (docs/apps/agent-platform.md,
"Main agent Control UI").

The gateway of main takes the user of each request from a header that the
internal ingress sets, and `allowUsers` of its trusted proxy auth names the
one user it admits. The value comes from OPENCLAW_UI_ALLOW_USER, a key of
the Secret of the pod, so it is not in git.

At the pinned release (src/gateway/auth.ts and src/config/env-substitution.ts
at 2026.11.3) the gateway trims the header value and compares it with each
entry by exact match, with the case as written. A `${VAR}` in an array entry
is replaced from the env. An env var that is not set or is empty leaves the
placeholder text in the list, with a warning only, so the list admits no
real user.

The container test starts the gateway of the agent image with the real
config on a network with no way out, and asks its startup probe from another
container. The probe gives its details (`uptimeMs`) only to a caller that
the auth admits, so the body shows the answer of the auth. Without Docker it
is skipped, except in CI, where it fails.
"""

import json
import os
import shutil
import subprocess
import uuid

from test_hand_written_rules import docker, scratch
from test_main_agent_backup import CONFIG, FAKE, container, projected, run, wait_for

USER = FAKE["OPENCLAW_UI_ALLOW_USER"]
# Each case: the user header (None: no header), and whether the gateway
# with the key set admits it.
CASES = {
    "the user": (USER, True),
    "the user with spaces around": (f"  {USER} ", True),
    "the user in capitals": (USER.upper(), False),
    "another user": ("other@example.invalid", False),
    "no user header": (None, False),
}
CLIENT = r"""
const cases = JSON.parse(process.env.CASES);
const out = {};
(async () => {
  for (const [name, user] of Object.entries(cases)) {
    // As the ingress sends them: the address of the client, and the two
    // headers that the config requires.
    const headers = {"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https",
                     "x-forwarded-host": "ui.example.invalid"};
    if (user !== null) headers[process.env.USER_HEADER] = user;
    const r = await fetch(`http://${process.env.GATEWAY}:${process.env.PORT}/startupz`, {headers});
    out[name] = {status: r.status, admitted: "uptimeMs" in JSON.parse(await r.text())};
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def ask(tool: str, image: str, network: str, gateway: str, cases: dict) -> dict:
    config = json.loads(CONFIG.read_text())["gateway"]
    env = {"CASES": json.dumps(cases), "GATEWAY": gateway, "PORT": str(config["port"]),
           "USER_HEADER": config["auth"]["trustedProxy"]["userHeader"]}
    args = [tool, "run", "--rm", "--network", network, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "-u", "1000:1000"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    out = subprocess.run([*args, "--entrypoint", "node", image, "-e", CLIENT],
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_gateway_admits_only_the_user_of_the_key():
    tool = docker()
    base = scratch()
    agent = container("agent")
    image = os.environ.get("RESTORE_TEST_IMAGE", agent["image"])
    network = f"main-ui-{uuid.uuid4().hex[:8]}"
    names = {"set": f"main-ui-set-{uuid.uuid4().hex[:8]}", "unset": f"main-ui-unset-{uuid.uuid4().hex[:8]}"}
    try:
        subprocess.run([tool, "network", "create", "--internal", network], check=True, capture_output=True,
                       timeout=60)
        subnet = subprocess.run(
            [tool, "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network],
            check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        base_env = {e["name"]: e["value"] for e in agent["env"] if "value" in e} | FAKE
        base_env["OPENCLAW_INGRESS_CIDR"] = subnet
        for key, name in names.items():
            work = base / key
            work.mkdir()
            work.chmod(0o755)
            config = projected(work)
            data, tmp = work / "data", work / "tmp"
            for path in (data, tmp):
                path.mkdir()
                # The mode of the claim root: OpenClaw 2026.11.10 refuses a
                # world writable ancestor with no sticky bit.
                path.chmod(0o1777)
            env = {k: v for k, v in base_env.items() if key == "set" or k != "OPENCLAW_UI_ALLOW_USER"}
            mounts = [(config, "/home/node/config", True), (data, "/home/node/data", False),
                      (tmp, "/tmp", False)]
            run(tool, name, ["--network", network], env, image, None, mounts)
        for name in names.values():
            wait_for(tool, name, r"\[gateway\] ready", 180)
        cases = {case: user for case, (user, _) in CASES.items()}
        got = ask(tool, image, network, names["set"], cases)
        assert {case: r["admitted"] for case, r in got.items()} == {c: ok for c, (_, ok) in CASES.items()}, got
        assert all(r["status"] == 200 for r in got.values()), got
        # With the key missing, the gateway keeps the placeholder and admits
        # no real user.
        got = ask(tool, image, network, names["unset"], {"the user": USER})
        assert got == {"the user": {"status": 200, "admitted": False}}, got
    finally:
        for name in names.values():
            subprocess.run([tool, "rm", "-f", name], capture_output=True, timeout=60, check=False)
        subprocess.run([tool, "network", "rm", network], capture_output=True, timeout=60, check=False)
        shutil.rmtree(base, ignore_errors=True)
