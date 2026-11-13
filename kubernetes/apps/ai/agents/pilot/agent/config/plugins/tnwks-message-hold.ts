// The hold of an outward message (R4). A plugin file of the product, loaded
// from the read-only config (plugins.load.paths in openclaw.json).
//
// Before the agent's message tool sends to an address that the agent names
// itself (target, to, channelId or targets), or broadcasts, the product asks
// the owner in the chat: allow once, or deny. With no answer in 10 minutes,
// the call is denied. A normal reply to the chat names no address and is not
// held. Commands in the sandbox are not held here: the exec approvals of the
// product cover host commands only, and the pilot runs no host command.
//
// One log line for each decision: "message-hold decision=<decision>".
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

function namesAddress(params: Record<string, unknown>): boolean {
  for (const key of ["target", "to", "channelId"]) {
    const value = params[key];
    if (typeof value === "string" && value.trim() !== "") return true;
  }
  const targets = params.targets;
  return Array.isArray(targets) && targets.some((t) => typeof t === "string" && t.trim() !== "");
}

export default definePluginEntry({
  id: "tnwks-message-hold",
  name: "tnwks message hold",
  description: "Ask the owner before the agent sends a message to an address it names.",
  register(api) {
    api.on("before_tool_call", (event) => {
      if (event.toolName !== "message") return;
      const params = event.params ?? {};
      const action = typeof params.action === "string" ? params.action : "";
      if (action !== "broadcast" && !namesAddress(params)) return;
      return {
        requireApproval: {
          title: "Send a message",
          description: `The agent asks to run message.${action || "send"} to an address it named. Allow it once, or deny it.`,
          severity: "warning",
          allowedDecisions: ["allow-once", "deny"],
          timeoutMs: 600_000,
          onResolution(decision) {
            console.log(`message-hold decision=${decision}`);
          },
        },
      };
    });
  },
});
