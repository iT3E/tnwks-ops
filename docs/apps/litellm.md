# LiteLLM

An OpenAI-compatible proxy in `ai` (WSL), `litellm.ai.svc.cluster.local:4000`,
in front of local models (Ollama on the WSL host) and Claude Sonnet on AWS
Bedrock. Manifests: `kubernetes/apps/ai/litellm/`. Its state (keys, spend)
is the cnpg `litellm` database.

| Model | Backend |
| --- | --- |
| `gemma3:12b`, `llama3.1:8b`, `qwen3:8b` | Ollama on the WSL host's GPU (`OLLAMA_BASE_URL`) |
| `claude-sonnet` | Bedrock, `us.anthropic.claude-sonnet-5-5` (Converse API, us-east-1) |

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
  `{"alias", "key", "models"}`.
- The `ensure-keys` sidecar (`app/scripts/ensure_keys.py`) creates each key
  with that exact value, or updates its models if it exists, then re-checks
  every 6 hours. Keys not in the list are left alone. Reloader restarts the
  pod when the Secret changes, so an edit applies on the next reconcile.
- The app gets the same value in its own Secret.

| Alias | Models | Used by |
| --- | --- | --- |
| `transcriber` | `claude-sonnet` | the transcriber's daily summaries (`docs/apps/transcriber.md`) |

To add one: generate a value (`sk-<app>-` and random hex), add the entry to
`keys.json`, and put the value in the app's Secret.
