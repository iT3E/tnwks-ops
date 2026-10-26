# MikroTik changes via CI

The RouterOS root module (`infrastructure/terraform/environments/prod/mikrotik`)
follows the same flow as the AWS root: plan on the PR, apply on merge, no manual
approval. The router's REST API is only reachable over a VPN, so the runs
happen on a self-hosted runner in the WSL cluster, which reaches the router
through its own WireGuard listener.

```
PR / merge to main
  -> mikrotik-plan.yaml / mikrotik-apply.yaml, runs-on: tnwks-ops-runner
     (ARC scale set, WSL cluster, tools namespace)
       runner pod
         wireproxy sidecar  --wg03 udp/51822-->  RB5009 10.10.32.1
           127.0.0.1:8443  ----------------->  REST 443 (nothing else)
         runner: terraform plan|apply
           state:   TFC workspace tnwks-mikrotik-prod (local execution)
           secrets: secrets.sops.yaml, via the CI-only age key
```

## Pieces

| Piece | Where | Notes |
| --- | --- | --- |
| Listener `wg03`, zone `vpn-ci` | generator `EXTRA_WIREGUARD` | `10.10.32.1/24`, one peer (`10.10.32.10`). Its own zone, not a `wg01` peer: `vpn -> *` defaults to accept, which a CI identity must not inherit. `vpn-ci` has no zone pairs (the terminal forward drop covers it) and one input accept, REST 443. WireGuard 51822 is accepted from the LAN only, never the WAN |
| Runner scale set `tnwks-ops-runner` | `kubernetes/apps/tools/actions-runner-controller/ops-runner/` | `minRunners: 0`, `maxRunners: 1`. Same GitHub App as `sites-builder`, installed on both repos |
| wireproxy sidecar | same pod | Userspace WireGuard (no `NET_ADMIN`, TUN or kernel module). Exposes the router's REST API as `https://127.0.0.1:8443` inside the pod; the runner waits for its `/readyz`, which pings the router through the tunnel |
| CI-only age key | `.sops.yaml` (first rule), `secret.sops.yaml` | Decrypts `environments/prod/mikrotik/*.sops.yaml` and nothing else in the repo. The runner gets it as `SOPS_AGE_KEY` |
| Endpoint override | `variables.tf` (`routeros_url`) | CI sets `TF_VAR_routeros_url=https://127.0.0.1:8443`; unset locally, where the SOPS value (`wg01`) applies |
| TFC token | repo secret `TF_API_TOKEN` | The one the AWS workflows already use. HCP Terraform's free plan has no custom teams, so no workspace-scoped token exists |
| Workflows | `mikrotik-{plan,apply}.yaml`, `.github/actions/mikrotik-terraform` | Shared plan/apply logic. One concurrency group, so runs never overlap |

## Security model

This repo is public, and a self-hosted runner executes whatever a workflow
tells it to inside the LAN. The controls:

1. **No fork or third-party code on the runner.** The plan job runs only when
   the PR comes from this repo and from the owner. Fork PRs need approval for
   any workflow at all (repo setting: all external contributors).
2. **Apply runs only from `main`.** The `prod` environment accepts deployments
   from protected branches only, and `main` takes changes through PRs.
3. **The runner can reach the router's REST API and nothing else.** No
   forwarding from `vpn-ci`, and the tunnel's `AllowedIPs` is the router's
   `wg03` address.
4. **Narrow secrets.** The CI age key decrypts only the MikroTik secrets.
   `permissions: {}` by default, `persist-credentials: false`.
5. **Public logs show no plan body.** The composite action prints only the
   counts and addresses of changing resources, and Terraform's error blocks
   on failure. The full plan never reaches the job log or the PR.

## Local runs

Unchanged: `task mikrotik:plan` / `task mikrotik:apply` over `wg01`, for
break-glass or when CI is down. Both use the same TFC state, and the
workspace lock keeps them from overlapping with CI.
