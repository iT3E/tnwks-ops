# LiteLLM

An OpenAI-compatible proxy in `ai` (WSL),
`litellm-app.ai.svc.cluster.local:4000`, in front of local models (Ollama on
the WSL host), Claude Sonnet on AWS Bedrock, and Claude Opus for the agent
platform (through the model bridge on the Claude plan, or on Bedrock).
Manifests: `kubernetes/apps/ai/litellm/`. Its state (keys, spend) is the cnpg
`litellm` database.

| Model | Backend |
| --- | --- |
| `gemma3:12b`, `llama3.1:8b`, `qwen3:8b` | Ollama on the WSL host's GPU (`OLLAMA_BASE_URL`) |
| `claude-sonnet` | Bedrock, `us.anthropic.claude-sonnet-5-5` (Converse API, us-east-1) |
| `claude-opus-bridge-main`, `claude-opus-bridge-finance`, `claude-opus-bridge-workflows` | The model bridge (`claude-opus-5-5` on the Claude plan), each with the bridge key of its trust domain |
| `claude-opus-bedrock` | Bedrock, `us.anthropic.claude-opus-5-5` (Invoke API, us-east-1) |
| `agents-main`, `agents-finance`, `agents-workflows`, `agents-pilot` | Names, not entries: each points at one `claude-opus-*` entry ([The agent platform](#the-agent-platform)) |

## Prompt caching

Claude only caches the parts of a prompt marked with `cache_control`, and
most clients mark nothing. So `litellm_settings.enable_anthropic_prompt_caching`
is on: for every Claude model (Anthropic, Bedrock, Vertex AI, Azure AI),
LiteLLM marks the system prompt and the last turn. A repeated prefix within
5 minutes is read back at a tenth of the input price, and a growing
conversation only writes its new turns. Other providers are untouched
(OpenAI and Gemini cache on their own; Ollama has nothing to cache).

- A client that sends its own `cache_control` keeps its own marks; the
  defaults stand down.
- A cache write costs 1.25x the input price, so a one-off call that nothing
  reuses within 5 minutes pays 25% more on its prompt.
- There is no off switch per request or per key while the global flag is on
  (a key's `enable_prompt_caching` can only turn it on). Opting a model out
  means turning the flag off and opting the others in per key.
- The cache is per AWS account, shared by every key on these credentials.

Check it in the spend logs (`/spend/logs?summarize=false`):
`metadata.usage_object` has `cache_creation_input_tokens` (writes) and
`cache_read_input_tokens` (reads) per request.

## The agent platform

Every model call of an agent of the agent platform
(`docs/apps/agent-platform.md`) comes here, to the name of its trust domain.
The provider behind each name is Claude Opus on the owner's Claude plan,
through the model bridge, or Claude Opus on Bedrock. Only a person moves a
name from one to the other, by one line in git.

### The entries

| Entry | Provider | Credential |
| --- | --- | --- |
| `claude-opus-bridge-main` | The model bridge, `anthropic/claude-opus-5-5` with `api_base` the bridge Service | `BRIDGE_KEY_MAIN` |
| `claude-opus-bridge-finance` | The same | `BRIDGE_KEY_FINANCE` |
| `claude-opus-bridge-workflows` | The same | `BRIDGE_KEY_WORKFLOWS` |
| `claude-opus-bedrock` | Bedrock, `bedrock/invoke/us.anthropic.claude-opus-5-5` (cross-region inference profile, us-east-1) | The Bedrock credentials of the pod |

- **The bridge entries.** The model bridge
  (`http://model-bridge.ai.svc.cluster.local:8080`, `POST /v1/messages`)
  holds the sign-in of the plan; this pod never does. LiteLLM sends the
  `api_key` of an `anthropic/` entry with an `api_base` in the `x-api-key`
  header, and the bridge knows the trust domain of a call by that key. Each
  bridge key is in `litellm-secret` (an env var of the `app` container) and,
  with the same value, in `model-bridge-secret`. The keys are 32 characters
  or more and don't start with `sk-ant-` (the bridge README has why).
  `forward_llm_provider_auth_headers` stays off, so the key of the caller
  never goes on to the bridge.
- **The Bedrock entry** uses the Invoke API, not Converse: on `/v1/messages`
  LiteLLM sends a `bedrock/invoke/` Claude model to Bedrock in the Messages
  format as it came (tools, thinking, cache marks), while a
  `bedrock/converse/` model goes through its chat-completion translation.
  The agents call `/v1/messages`.
- Agent keys can't call an entry by its own name; only the master key can.

### The names

`router_settings.model_group_alias` in `app/configmap.yaml` gives each trust
domain one name, on one line, pointing at one entry:

```yaml
model_group_alias:
  agents-main: claude-opus-bridge-main
  agents-finance: claude-opus-bridge-finance
  agents-workflows: claude-opus-bridge-workflows
  agents-pilot: claude-opus-bridge-main
```

- The pilot has no bridge key of its own: the bridge serves the trust
  domains `main`, `finance` and `workflows`. Its name points at the main
  entry, the path the main agent takes later.
- The router resolves an alias on `/v1/messages` like on every other model
  route, and the key check takes a key that lists the alias name.

### Moving a name, and back

1. In a pull request, change the one line of the name:
   `agents-main: claude-opus-bedrock` (or back to
   `claude-opus-bridge-main`). The finance agent follows the main agent, so
   the same pull request changes `agents-finance` too.
2. Merge it. Flux applies the ConfigMap, and Reloader restarts LiteLLM:
   calls to every model fail for the time of one start.
3. The way back is a revert of that merge.

Nothing else changes: no agent pod, no key, no secret. A conversation that
comes back to the bridge after a move starts there again, with its history
as one text (the bridge README).

### Nothing moves without a person

- This config has no `fallbacks`, `context_window_fallbacks`,
  `content_policy_fallbacks` or `default_fallbacks`, and no `order` on an
  entry. Keep it that way.
- Each agent key lists its one name. A call to any other name or entry gets
  403.
- A caller can name fallbacks in a request (`fallbacks` in the body, or in
  `router_settings_override`). LiteLLM checks each one against the key's
  model list before the call, so a fallback to another entry gets 403.
- The config of each agent names one model and has no fallback list.
- The `router_settings` that the admin UI can save in the database merge
  over this file. Don't save any there; the master key is the owner's.

### Test a name

With the master key in a variable and never on the screen:

```sh
kubectl -n ai port-forward svc/litellm-app 4000:4000 &
KEY=$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -s localhost:4000/v1/messages \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' -H 'Content-Type: application/json' \
  -d '{"model":"agents-pilot","max_tokens":32,"messages":[{"role":"user","content":"Say ok"}]}'
```

The same call with an agent key works for its own name only; with
`"fallbacks":["claude-sonnet"]` added, it gets 403.

## Bedrock credentials

Bedrock is in its own AWS account, outside Terraform by design. LiteLLM
uses the account's existing IAM Roles Anywhere setup (the same certificate,
trust anchor, profile and role as the workstation's Bedrock profile); nothing
was created in AWS for it.

- `app/secret-roles-anywhere.sops.yaml`: the client certificate and key, and
  the trust anchor, profile and role ARNs.
- The `aws-credentials` sidecar runs `aws_signing_helper serve`: it exchanges
  the certificate for short-lived STS credentials, refreshes them, and serves
  them as an EC2 metadata endpoint on `127.0.0.1:9911`, inside the pod only.
  LiteLLM's boto3 finds it through `AWS_EC2_METADATA_SERVICE_ENDPOINT`.
- The helper binary is fetched at its pinned release and checked against its
  SHA-256 by the `fetch-signing-helper` init container, as
  `aws-backup-credentials` does.
- **It expires 2027-03-28.** An alert in `app/prometheusrule.yaml` fires
  from 2027-02-26. Renewing: replace the PEM files in the Secret, and move
  the date in the rule.

## Keys

Each app gets its own LiteLLM virtual key, limited to the models it needs.
Keys are declared, not created by hand:

- `app/secret-keys.sops.yaml` holds `keys.json`, a list of
  `{"alias", "key", "models"}`. An entry may also set a spend limit:
  `"max_budget"` (US dollars) and `"budget_duration"` (when the spend
  resets, such as `"30d"`). LiteLLM refuses the key's calls once it has spent
  the limit, and counts the spend in its database.
- The `ensure-keys` sidecar (`app/scripts/ensure_keys.py`) creates each key
  with that exact value, or updates its models (and its limit, when the entry
  sets one) if it exists, then re-checks every 6 hours. Keys not in the list
  are left alone. Removing a limit from an entry doesn't remove it from the
  key: change it in the admin UI. Reloader restarts the pod when the Secret
  changes, so an edit applies on the next reconcile.
- The app gets the same value in its own Secret.

| Alias | Models | Used by |
| --- | --- | --- |
| `transcriber` | `claude-sonnet` | the transcriber's daily summaries (`docs/apps/transcriber.md`) |

To add one: generate a value (`sk-<app>-` and random hex), add the entry to
`keys.json`, and put the value in the app's Secret.

### Keys of the agent platform

Each trust domain of the agent platform (`docs/apps/agent-platform.md`) gets
one key of its own, whose model list is the name of that trust domain and
nothing else, and no key serves two trust domains. The agents of a trust
domain call `http://litellm-app.ai.svc.cluster.local:4000` with it (the chart
names the Service `litellm-app`).

| Alias | Models | Trust domain (namespace) | The agent's copy |
| --- | --- | --- | --- |
| `agents-main` | `["agents-main"]` | `agents-main` | Secret `agent-secret`, key `LITELLM_API_KEY` |
| `agents-finance` | `["agents-finance"]` | `agents-finance` | Secret `agent-secret`, key `LITELLM_API_KEY` |
| `agents-workflows` | `["agents-workflows"]` | `agents-workflows` | Comes with the run path of the workflow agents |
| `agents-pilot` | `["agents-pilot"]` | `agents-pilot` | Secret `agent-secret`, key `LITELLM_API_KEY` |

- The owner adds each entry to `keys.json`, with its value
  (`sk-agents-<name>-` and random hex), when the agent of that trust domain
  is ready to use it. The same value goes in that trust domain's own Secret.
- No spend limit for now: the entries set no `max_budget` until the pilot
  has measured the use. LiteLLM counts spend at the API list price, also on
  the plan, so the spend logs show use and not money paid.
- Never leave `models` empty: a key with an empty list can call every model.
