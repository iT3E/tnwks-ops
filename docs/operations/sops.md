# SOPS

Every secret in the repo is a `*.sops.yaml` file. `.sops.yaml` sets the
recipients per path; first match wins:

| Recipient | Decrypts | Used by |
| --- | --- | --- |
| the three age keys (named in `.sops.yaml`'s header) | every file | you; Flux on the WSL cluster (`sops-age` Secret) |
| AWS KMS `kms-sops` via `iam-role-sops` | every file | you, through IAM Roles Anywhere |
| CI age key | `environments/prod/mikrotik/` and `infrastructure/edgeos/` only | the self-hosted runner ([`../network/ci.md`](../network/ci.md)) |

For Kubernetes manifests only `data` and `stringData` are encrypted, so key
names stay readable.

## Day to day

Decryption uses your age key (`SOPS_AGE_KEY_FILE`, set by `.envrc`), so it
needs no AWS access:

```bash
sops path/to/secrets.sops.yaml                       # edit in $EDITOR
sops -d --extract '["key"]' path/to/secrets.sops.yaml
sops --set '["key"] "value"' path/to/secrets.sops.yaml
```

Encrypting a new file, `sops updatekeys` and `sops rotate` also need the KMS
recipient. Inside the repo `bin/sops` (on PATH via `.envrc`) runs sops with the
`tnwks-sops` AWS profile for that process only; set it up once with
`task aws:ra-cert` and `task aws:ra-profile`
([`../../infrastructure/terraform/environments/prod/aws/rolesanywhere/README.md`](../../infrastructure/terraform/environments/prod/aws/rolesanywhere/README.md)).

New files: name them `*.sops.yaml` under a path `.sops.yaml` covers, then
`sops -e -i <file>`.

## After changing recipients

Re-key every file so its recipients match `.sops.yaml` again:

```bash
for f in $(git ls-files '*.sops.yaml'); do sops updatekeys -y "$f"; done
```

Never decrypt files in place to bulk-edit them: the plaintext lands in the
working tree, one `git add` away from a commit. Edit with `sops <file>`, which
keeps the decrypted copy in a temporary file.
