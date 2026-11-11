# LiteLLM

An OpenAI-compatible proxy in `ai` (WSL),
`litellm-app.ai.svc.cluster.local:4000`, in front of local models (Ollama on
the WSL host), Claude Sonnet on AWS Bedrock and Claude Opus on the Anthropic API. Manifests: `kubernetes/apps/ai/litellm/`. Its state (keys, spend)
is the cnpg `litellm` database.

| Model | Backend |
| --- | --- |
| `gemma3:12b`, `llama3.1:8b`, `qwen3:8b` | Ollama on the WSL host's GPU (`OLLAMA_BASE_URL`) |
| `claude-sonnet` | Bedrock, `us.anthropic.claude-sonnet-5-5` (Converse API, us-east-1) |
| `claude-opus-plan` | Anthropic API, `claude-opus-5-5`, with the token of the owner's Claude plan |

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

## Claude on the Anthropic API

`claude-opus-plan` calls the Anthropic API with a token of the owner's Claude
plan, so its use counts against the plan and not against Bedrock. It is here
for the agent platform (`docs/apps/agent-platform.md`), as a first test: no
key in `keys.json` lists the model, so only the master key can call it.

- The token is `ANTHROPIC_PLAN_TOKEN` in `app/secret.sops.yaml`. The owner
  makes it with `claude setup-token` and adds it with
  `sops kubernetes/apps/ai/litellm/app/secret.sops.yaml`. Reloader restarts
  the pod when the Secret changes.
- The pod needs the value to start, like the other values of that Secret.
- LiteLLM sends a plan token as a bearer token with the OAuth beta header;
  an API key in the same place goes out as `x-api-key`. Nothing else in the
  entry changes between the two.
- Anthropic decides whether it serves a plan token from a client that is not
  one of its own apps, and it can change that. A refused call comes back as
  an error from this model only.

Test it from a workstation, with the master key in a variable and never on
the screen:

```sh
kubectl -n ai port-forward svc/litellm-app 4000:4000 &
KEY=$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -s localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-plan","max_tokens":32,"messages":[{"role":"user","content":"Say ok"}]}'
```

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
one key of its own, with a model list and a spend limit, and no key serves two
trust domains. The agents of a trust domain call
`http://litellm-app.ai.svc.cluster.local:4000` with it (the chart names the
Service `litellm-app`).

| Alias | Trust domain (namespace) |
| --- | --- |
| `agents-main` | `agents-main` |
| `agents-finance` | `agents-finance` |
| `agents-workflows` | `agents-workflows` |
| `agents-pilot` | `agents-pilot` |

The owner adds each entry to `keys.json`, with its value (`sk-agents-<name>-`
and random hex), its models and its limit, when the agent of that trust
domain is ready to use it. The same value goes in that trust domain's own
Secret, next to the agent's manifests.
