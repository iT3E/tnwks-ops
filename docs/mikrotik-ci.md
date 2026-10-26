# MikroTik changes via CI (planned, after cutover)

Today the RouterOS root module is planned and applied from a workstation
(`task mikrotik:{plan,apply}`). The goal is the same PR flow as the AWS root:
plan on the PR, apply on merge. The router is only reachable from the LAN, so
the runs need a runner inside the network, and router management is VPN-only,
so that runner needs its own way in.

## Shape

```
PR / merge to main
  -> GitHub Actions job, runs-on: tnwks-ops-runner (ARC scale set, WSL cluster)
       wireguard sidecar  --wg03-->  RB5009 10.10.32.1:443 (REST)
       terraform plan|apply  (state in TFC workspace tnwks-mikrotik-prod,
                              execution mode local: the run happens on the runner)
```

## Pieces

| Piece | Where | Notes |
| --- | --- | --- |
| Runner scale set `tnwks-ops-runner` | `kubernetes/apps/tools/actions-runner-controller/runner/` | Second scale set next to `sites-builder`, scoped to this repo. The existing GitHub App is installed on `tnwks-ops` too |
| WireGuard sidecar | same pod | Shares the runner's network namespace; needs `NET_ADMIN`. Config (private key, peer) from a SOPS secret. `AllowedIPs` is the router's `wg03` address only |
| Listener `wg03`, zone `vpn-ci` | `modules/mikrotik` + generator | Its own listener and zone, not a `wg01` peer: `vpn -> *` defaults to accept, which a CI identity must not inherit. `vpn-ci` gets an input accept for REST 443 and no forward pairs, so the terminal drop covers everything else |
| CI-only SOPS recipient | `.sops.yaml` | A dedicated age key that decrypts only `environments/prod/mikrotik/secrets.sops.yaml`, mounted into the runner. A leaked CI key exposes the router's secrets, not the repo's |
| TFC token | repo secret | A team token scoped to `tnwks-mikrotik-prod` rather than the org token |
| Workflows `mikrotik-plan.yaml` / `mikrotik-apply.yaml` | `.github/workflows/` | Same gating as the AWS workflows (below) |

## Security model

This repo is public, and a self-hosted runner executes whatever a workflow
tells it to inside the LAN. The controls, in order:

1. **No fork code on the runner.** Plan runs only when the PR's source repo is
   this repo and its author is the owner (`head.repo.full_name ==
   github.repository`), the same gate the AWS plan uses. Fork PRs never get a
   job on the self-hosted runner.
2. **Fork PR workflows need approval** for all external contributors (repo
   setting), so even a workflow change in a fork PR cannot run unreviewed.
3. **Apply waits for the `prod` environment's reviewers**, and runs only on
   `push` to `main`, which branch protection limits to merged PRs with a
   passing secret scan and no force-pushes.
4. **The runner can only reach the router's REST API.** `wg03` / `vpn-ci` has no
   forward access, and the runner's tunnel carries only the router address.
5. **Least privilege everywhere else:** `permissions: {}` by default, a
   CI-only age key, a workspace-scoped TFC token.

## Order of work

1. After cutover, with the router stable: add `wg03`/`vpn-ci` to the module and
   generator; apply from the workstation.
2. CI age recipient in `.sops.yaml`; re-encrypt the MikroTik secrets file.
3. Runner scale set + WireGuard sidecar (Flux).
4. Workflows, first as plan-only; switch on apply once plans are trusted.
