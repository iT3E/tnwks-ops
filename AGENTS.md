## Repository purpose

Home-infrastructure monorepo. Two peer Talos/Kubernetes clusters share one
Flux-driven source of truth, with Ansible/Terraform for the bits below
Kubernetes. There are no application source trees here — every directory
holds infrastructure config, manifests, or playbooks.

| Cluster         | Where                               | CNI                                         | Storage default                                                | LB                                           | Notes                                                                   |
| --------------- | ----------------------------------- | ------------------------------------------- | -------------------------------------------------------------- | -------------------------------------------- | ----------------------------------------------------------------------- |
| `homelab-wsl`   | Talos-in-Docker on the WSL2 desktop | **Flannel**                                 | `local-path`                                                   | MetalLB on the docker bridge (`10.5.0.0/24`) | USB passthrough via `usbipd-win` for Frigate/Coral + Z-Stick            |
| `homelab-ms-01` | 3× MS-01 bare-metal Talos           | **Cilium** (kube-proxy replacement, L2/BGP) | `ceph-block` (Rook), `nfs-bulk` (democratic-csi → TerraMaster) | Cilium L2/BGP on VLAN 910 (`10.10.91.50–60`) | Public ingress via cloudflared; runs Rook Ceph, Loki/Thanos/Vector, NFD |

Cilium cannot run on WSL — `clean-cilium-state` requests `CAP_SYS_MODULE` which
the WSL2 kernel rejects in nested containers ([cilium#13768](https://github.com/cilium/cilium/issues/13768)).
That single fact drives most of the WSL/MS-01 divergence (no
`CiliumNetworkPolicy` ⇒ no `pod-gateway` ⇒ no qbittorrent/prowlarr on WSL).

## Common commands

Workstation prereqs are installed by `task init` (Homebrew formulas listed in
`Taskfile.yml`). The repo uses `direnv` (`.envrc`) to set
`KUBECONFIG=${ROOT_DIR}/kubeconfig` and `SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt`.
Per-cluster operations override `KUBECONFIG` to `~/.kube/config-homelab-{wsl,ms-01}`.

```bash
# Bootstrap a cluster end-to-end (Talos → CNI → SOPS → Flux).
# GITHUB_TOKEN must be exported (repo scope on the PAT).
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')
task bootstrap:wsl     # talosctl cluster create docker + Flannel
task bootstrap:ms-01   # talosctl apply-config to MS-01 nodes + Cilium

# Force Flux reconcile on a cluster
task cluster:reconcile:wsl
task cluster:reconcile:ms-01

# Snapshot of cluster health (nodes, kustomizations, HRs, certs, ingresses, pods)
task cluster:resources

# Restart all failed HelmReleases (suspend+resume)
task cluster:hr-restart

# WSL Talos lifecycle (Docker-mode only)
task talos:status:wsl
task talos:reset:wsl       # destroy + recreate
task talos:destroy:wsl

# Terraform — the local task targets Cloudflare *only*. AWS state is in
# Terraform Cloud (workspace tnwks-ops-aws-prod) and is driven by the
# .github/workflows/terraform-{plan,apply}.yaml on PRs/pushes touching
# infrastructure/terraform/aws/accounts/prod/**.
task terraform:plan
task terraform:apply

# Pre-commit (yamllint + prettier per repo config)
task precommit:run
```

To run a single Ansible playbook directly (e.g. when re-running just the
SOPS-key step), use the inventory under `infrastructure/ansible/inventory/`:

```bash
cd infrastructure/ansible
ansible-playbook -i inventory/wsl.yml playbooks/03-sops-keys.yml
```

## Change workflow

All changes go through a PR and are squash-merged to `main` — no direct
pushes. There is no central "deploy" step; validation happens in whatever
system actually owns the change:

> **`main` is the single source of truth for cluster state, and Flux is the
> only thing that mutates the cluster.** This is a fix-forward repo: get as
> confident as you can *before* the PR using **non-mutating** checks, then
> merge and let Flux reconcile. If it breaks, fix forward in a follow-up
> commit — never reach into the cluster to patch around it.
>
> Pre-merge confidence is built **without touching live state**:
> `kustomize build` (or `flux build`), `kubectl apply --dry-run=server`,
> `sops -d` to confirm a secret decrypts, and registry/CRD/version checks.
>
> **Do NOT** `kubectl apply`/`create`/`patch`/`delete`, `helm install`, or
> otherwise hand-apply a feature branch onto the cluster to "test" it — that
> creates drift `main` can't describe and resources Flux will later prune or
> fight. The same rule covers live workarounds: don't `kubectl patch` a
> resource, hand-create a database, or annotate a Secret to make something
> work. If a step genuinely cannot be expressed in git (e.g. CNPG
> `postInitApplicationSQL` only runs at first boot), find the declarative
> equivalent that reconciles on a running cluster (e.g. the CNPG `Database`
> CRD) rather than running the imperative command by hand.

- **Kubernetes (`kubernetes/**`)** — Flux reconciles on the next interval
  (30m) after the squash-merge lands. To validate immediately, force a
  sync and inspect the affected resources:

  ```bash
  task cluster:reconcile:<cluster>
  flux get kustomizations -A | grep <name>
  flux get hr -A | grep <name>
  task cluster:resources
  ```

  Per-resource errors surface in `kubectl describe` / `kubectl logs` on
  the affected pods. Manifests reach both clusters from the same `main`,
  so check `homelab-wsl` and `homelab-ms-01` if the change touches a
  shared app.

- **Terraform AWS (`infrastructure/terraform/aws/accounts/prod/**`)** —
  the PR triggers a speculative plan via
  `.github/workflows/terraform-plan.yaml` (Terraform Cloud workspace
  `tnwks-ops-aws-prod`); the plan summary is posted as a PR comment with
  a run link. Squash-merging triggers `terraform-apply.yaml`, which
  creates and auto-confirms a real run in the same workspace. Validate
  via the run link or the TFC UI.

- **Terraform Cloudflare (`infrastructure/terraform/cloudflare/**`)** —
  not wired into CI. After the PR merges, run `task terraform:apply`
  locally to push the change.

- **Ansible (`infrastructure/ansible/**`)** — playbooks run on demand
  from a workstation (`task bootstrap:<cluster>` or a direct
  `ansible-playbook` invocation). Not driven by CI; merging only ships
  the playbook source.

- **Talos (`talos/**`)** — machineconfig edits are inert until applied
  with `talosctl apply-config` (or the bootstrap playbook for a fresh
  cluster). Merging the YAML doesn't reach the node.

## Architecture

### Flux entry point per cluster

Flux is bootstrapped per cluster with `flux bootstrap github --path=kubernetes/clusters/<cluster>`.
Each cluster directory has the same shape:

```
kubernetes/clusters/<cluster>/
├── flux-system/              # Created by `flux bootstrap`; do not hand-edit
├── kustomization.yaml        # Aggregator (excludes flux-system/)
├── cluster-settings.yaml     # ConfigMap with per-cluster vars
├── cluster-secrets.sops.yaml # SOPS-encrypted Secret with per-cluster vars
├── meta.yaml                 # Flux Kustomization → kubernetes/flux/meta
├── apps.yaml                 # Flux Kustomization → ./apps with substituteFrom
└── apps/                     # Kustomize overlay listing which shared apps to deploy
```

`apps.yaml` ships a global `postBuild.substituteFrom` that pulls
`cluster-settings` (ConfigMap) and `cluster-secrets` (Secret) into envsubst
for every dependent Kustomization — so manifests reference values like
`${STORAGE_CLASS_DEFAULT}`, `${BULK_PVC_SIZE}`, `${SECRET_DOMAIN}`,
`${INGRESS_INTERNAL_ADDR}` instead of hardcoding cluster-specific values.

To opt a Kustomization _out_ of substitution (e.g. when its ConfigMap data
contains JS template-literal `${...}` that envsubst would mangle), label
the Flux Kustomization `substitution.flux.home.arpa/disabled: "true"` — see
`kubernetes/apps/auth/onboard/ks.yaml` for the canonical example.

### Shared apps + per-cluster overlays

`kubernetes/apps/<namespace>/<app>/` is the canonical place for app manifests.
Each cluster's `kubernetes/clusters/<cluster>/apps/kustomization.yaml`
selects which of those to deploy. There are two ways the per-cluster split
shows up:

1. **Whole-namespace exclusion** — e.g. WSL omits `apps/storage` and
   `apps/networking/cloudflared`-driven public bits entirely.
2. **Per-namespace overlay** — when only a few apps in a namespace differ,
   the cluster's `apps/<namespace>/kustomization.yaml` re-lists the shared
   `ks.yaml` files it wants. See `kubernetes/clusters/wsl/apps/networking/`
   and `…/home-automation/`, `…/production/`.

MS-01-only adds (rook-ceph, loki/thanos/vector, NFD, cnpg backup) are
referenced explicitly from `kubernetes/clusters/ms-01/apps/kustomization.yaml`.

### App layout pattern

```
kubernetes/apps/<namespace>/<app>/
├── ks.yaml          # Flux Kustomization (or several, with dependsOn)
└── app/             # Kustomize overlay
    ├── kustomization.yaml
    ├── helmrelease.yaml      # Most apps use bjw-s/app-template
    ├── *.sops.yaml           # SOPS-encrypted Secrets (matched by .sops.yaml)
    └── …
```

`ks.yaml` files routinely declare `dependsOn` to gate reconciliation
(e.g. `oauth2-proxy` depends on `ingress-nginx-internal`; `cnpg-cluster`
depends on `cnpg-app`; `cnpg-cluster-prometheusrules` depends on
`kube-prometheus-stack`). When adding a new Flux Kustomization, set its
`dependsOn` for any CRD it relies on.

### Bootstrap chain (Ansible)

`task bootstrap:<cluster>` runs four idempotent playbooks against
`localhost`:

1. `01-talos-bootstrap.yml` — `talosctl cluster create docker` (WSL) or
   `talosctl apply-config` + `bootstrap` (MS-01). Writes
   `~/.kube/config-homelab-<cluster>`. On WSL, also augments worker-2 with
   USB device passthrough and a `/mnt/e/k8s-storage` bind mount via a
   Docker container recreate that preserves the original named volumes.
2. `02-cni-bootstrap.yml` — installs Flannel (WSL) or Cilium (MS-01).
3. `03-sops-keys.yml` — generates `~/.config/sops/age/homelab-<cluster>.txt`
   and creates the `sops-age` Secret in `flux-system`. **Back this key up.**
4. `04-flux-install.yml` — `flux bootstrap github` against the cluster's
   path. Requires `GITHUB_TOKEN`.

After step 4, **all changes are GitOps** — push to `main` and Flux reconciles.

### SOPS layout

`.sops.yaml` defines per-path rules with multiple recipients: legacy age
keys, an AWS KMS MRK in account `654654220436` (assumed via
`role/iam-role-sops`), and per-cluster age keys provisioned by
`03-sops-keys.yml`. When adding a new SOPS file, name it `*.sops.yaml`
under one of the matched path prefixes (`kubernetes/`, `ansible/`,
`terraform/`, `infrastructure/terraform/`, `packer/`) so the rules apply.
For Kubernetes paths only, `data` and `stringData` are encrypted (key
names stay in plaintext).

### Terraform

- `infrastructure/terraform/aws/accounts/prod/` — Cognito user pools (auth
  for ingresses), driven by **Terraform Cloud** workspace
  `tnwks-ops-aws-prod` via the GitHub Actions in `.github/workflows/terraform-{plan,apply}.yaml`.
  PRs from non-`iT3E` actors require approval in the `external-contributions`
  environment before plan runs. Provider assumes
  `arn:aws:iam::654654262098:role/tnwks-org-init-role`; ACM for Cognito
  custom domains uses an aliased `us_east_1` provider (Cognito custom
  domains require us-east-1 ACM regardless of pool region).
- `infrastructure/terraform/cloudflare/` — DNS + zone/account-level config,
  driven locally via `task terraform:{plan,apply}`.
- `infrastructure/terraform/modules/` — shared modules.
- `infrastructure/_archive/` — old PVE/Proxmox terraform + packer; not
  active.

### Talos

`talos/wsl/cluster-config.yaml` is a `--config-patch` consumed by
`talosctl cluster create docker`. `talos/ms-01/{controlplane,worker}.yaml`
plus `talos/ms-01/patches/` are applied with `talosctl apply-config`.

## Conventions

- YAML files start with `---`, two-space indent, prettier-formatted (see
  `.editorconfig`). Pre-commit runs yamllint + prettier.
- Manifests should reference cluster-settings vars (`${STORAGE_CLASS_DEFAULT}`,
  `${BULK_PVC_SIZE}`, etc.) instead of hardcoding cluster-specific values,
  so they remain portable between WSL and MS-01.
- Most app `HelmRelease`s use the `bjw-s` `app-template` chart from the
  HelmRepository in `kubernetes/flux/meta/helm/bjw-s.yaml`.
- The repo is public — do not introduce AI-attribution trailers, identifying
  commit metadata, or anything tying commits to non-public identity.
