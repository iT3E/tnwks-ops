# The token job of the App of the main agent

A CronJob in `agents-main` that makes an installation token of the App of
the main agent each 30 minutes and writes it into one Secret, which the
proxy of the code host server reads (Design Doc of effort 0003, section
4.2.6, "The credential of the code host server"; task 6.20). The private
key of the App stays in the pod of this job. The code host server, the
gateway and the agent never hold it (R2).

**Not listed yet.** The manifests are in git, and
`kubernetes/apps/ai/agents/main/kustomization.yaml` lists `./github-token/ks.yaml`
only with the key: the owner runs [the key step](#the-key), and the same
pull request lists the folder. Until a later step of task 6.20, the code
host server still reads the stopgap token of the owner, and nothing reads
the token of this job.

Manifests: `kubernetes/apps/ai/agents/main/github-token/`.

## Pieces

| Piece | Where |
| --- | --- |
| CronJob | `github-token`, at minutes 13 and 43, `scripts/github_token.py` in the pinned Python image (it has `openssl`) |
| The key | Secret `main-github-app`, key `private-key.pem`, in `app/secret.sops.yaml`, mounted as a file in the job only (mode 0440) |
| The token | Secret `github-mcp-app-token`, key `GITHUB_MCP_TOKEN`, in `app/secret-token.yaml`. Flux makes it once with the placeholder `pending` (`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`) and never writes it again, so the job owns its data |
| Account | `github-token`: `update` on the Secret `github-mcp-app-token` by name, and nothing else |
| Network policy | `github-token`: out to `api.github.com` on 443, to the API server, and DNS for that one name. Nothing comes in |

## A run

1. A JWT of the App, signed by `openssl` with the key file, that lives 10
   minutes at most (its start is one minute back, for clock skew). The
   script never reads the key.
2. The installation of the App on the ops repository. When it has less
   than the permissions below, the run fails. When it has more, or more
   repositories, the run logs `github-token result=app-drift what=<what>`
   and goes on: the token stays narrow.
3. One installation token for the ops repository only, with exactly these
   permissions (D138, D140): Metadata read, Contents write, Pull requests
   write, Actions read, Issues read and write. The run fails when the
   token that comes back has other permissions, another repository, or a
   life of more than one hour.
4. An update of the Secret `github-mcp-app-token`, with no read first.

One line for each run:

- `github-token result=ok secret=github-mcp-app-token expires=<time>`
- `github-token result=failed step=<config|jwt|installation|token|secret> <reason>`

The line never holds the token, the key or the body of an answer.

A failed run fails its Job, and `AgentPlatformCronJobFailed` fires. The
last token stays in the Secret, valid for at least 30 minutes after the
last good run. Run it now with
`kubectl -n agents-main create job --from=cronjob/github-token github-token-now`.

## The key

The key of the App enters once, at the terminal of the owner, and never
passes through a lead or agent session. The owner downloads a private key
of the App from its settings at the code host, then runs, from the root of
a checkout of the branch of the token job:

```bash
bin/main-app-key --app-id <the ID of the App> <path of the .pem>
```

- It checks the file with `openssl`: an RSA private key of 2048 bits or
  more, with no passphrase. It prints a flag only.
- It puts the key into `app/secret.sops.yaml` in the editor step of
  `bin/sops`, so the key is never an argument, an env var or a line of
  output. It reads the key back through `bin/sops -d --extract` in a pipe,
  compares it with the file, and prints a flag only.
- It sets the ID of the App (no secret) in `app/cronjob.yaml`.
- Its last line tells the owner to delete the download.

A file that holds a key stays as it is. `--replace` puts a new key in its
place, for a new key of the App. Then the owner deletes the old key in the
settings of the App.

## The way back

- **Stop the tokens at once:** the owner deletes the private key in the
  settings of the App, or suspends the installation. Each token that the
  job made then stops working within one hour at most, and the next run
  fails.
- **Take the job out:** remove the line `./github-token/ks.yaml`. Flux
  prunes the CronJob, the account and the two Secrets.

## Tests

`observability/tests/test_main_github_token.py` runs `bin/main-app-key` in
a copy of its files with a fake sops and throwaway keys, and checks that
no line of the key reaches the output, an argument or an env var. It runs
the script of the job against a fake API of the code host, which checks
the signature of the JWT, and a fake API server. Each check of a run has a
case that fails. It also reads the manifests: the permissions, the
schedule, the one account and Secret, the policy, and the listing with the
key.
