#!/usr/bin/env bash
# The proofs of the Flux lockdown with the controller of git
# (docs/operations/flux-lockdown.md).
#
#   uv run --project observability kubernetes/apps/flux-system/lockdown/tests/run.sh
#
# observability/tests/test_flux_lockdown.py runs it in CI. It starts a
# throwaway control plane in containers: etcd and kube-apiserver (the
# version of the clusters), a plain HTTP server that gives a test tree as
# the artifact of the GitRepository flux-system, and kustomize-controller
# (the image and the arguments of git) with a client certificate of the
# account flux-system/kustomize-controller, bound to cluster-admin as on the
# clusters, and kube-controller-manager with the controller that fills the
# aggregated roles (admin, which the children get) and the controllers of
# Deployments, ReplicaSets and StatefulSets (so a wait on health ends). It
# runs test_flux_lockdown.py in a plain Python image on that network, in
# three steps: Setup; NoFlag, with the arguments of a site with no flag; and
# Flag, after a restart with the arguments of a site with the flag of step
# 2e (--default-service-account, as git sets it).
#
# Nothing touches a real cluster: no kubeconfig of the workstation goes into
# a container, the network has no route out, and the tree holds no secret
# and no key of a site. The two age keys of step 2d are made for the run;
# sops encrypts placeholders to their public keys only, with no network,
# and never decrypts. Each container, the network, the keys and the
# certificates go at the end. KEEP=1 keeps them after a failure, the keys
# among them: they are throwaway keys of the run, for no site.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
upgrade="${here}/../../../system-upgrade/tuppr/upgrades/kubernetes.yaml"
version="${KUBE_VERSION:-$(sed -nE 's/^ +version: (v[0-9]+\.[0-9]+\.[0-9]+)$/\1/p' "${upgrade}" | head -n 1)}"
[[ "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "no Kubernetes version in ${upgrade}" >&2; exit 1; }
etcd_image="${ETCD_IMAGE:-registry.k8s.io/etcd:3.6.6-0}"
# The Python image of the checks of the agent platform.
python_image="docker.io/library/python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928"
# sops, the version of .github/workflows/terraform.yaml.
sops_image="ghcr.io/getsops/sops:v3.13.3@sha256:857f5a151ac0b2bfc55c1e4e5581d66fb8e268e4d106b38e74191f3bac9d58ea"

id="flux-lockdown-test-$$"
work="$(mktemp -d)"
pki="${work}/pki"
mkdir -p "${pki}"

finish() {
  local status=$?
  if ((status != 0)) && [[ "${KEEP:-}" == "1" ]]; then
    echo "kept: containers ${id}-*, network ${id}, files ${work}"
    exit "${status}"
  fi
  if ((status != 0)); then
    docker logs --tail 30 "${id}-apiserver" 2>&1 | sed 's/^/apiserver: /' || true
    docker logs --tail 60 "${id}-controller" 2>&1 | sed 's/^/controller: /' || true
  fi
  docker rm -f "${id}-etcd" "${id}-apiserver" "${id}-controllers" "${id}-source" "${id}-controller" \
    "${id}-tests" >/dev/null 2>&1 || true
  docker network rm "${id}" >/dev/null 2>&1 || true
  rm -rf "${work}"
  exit "${status}"
}
trap finish EXIT

# Step 2d: the keys of the run, and the Secrets of placeholders encrypted to
# their public keys, as the files of the sites are.
"${PYTHON:-python3}" "${here}/render.py" keys "${work}"
mkdir -p "${work}/enc"
for plain in "${work}"/plain/*.yaml; do
  folder="$(basename "${plain}" .yaml)"
  docker run --rm --network none --read-only --user "$(id -u):$(id -g)" -v "${work}/plain:/plain:ro" \
    "${sops_image}" encrypt --age "$(cat "${work}/plain/${folder}.age")" --encrypted-regex '^(data|stringData)$' \
    --input-type yaml --output-type yaml "/plain/${folder}.yaml" >"${work}/enc/${folder}.sops.yaml"
done
"${PYTHON:-python3}" "${here}/render.py" "${work}"
image="$(cat "${work}/image.txt")"
mapfile -t args <"${work}/args.txt"
# The arguments of a site with the flag of step 2e (git's own value).
mapfile -t args_flag <"${work}/args-flag.txt"

# Throwaway certificates: a CA, the API server, the admin of the test, the
# controller (the user name and groups of its service account), the token key.
(
  cd "${pki}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=flux-lockdown-test-ca" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout ca.key -out ca.crt 2>/dev/null
  cat >san.ext <<'EOF'
subjectAltName=DNS:apiserver,DNS:kubernetes,DNS:kubernetes.default.svc,IP:10.96.0.1,DNS:localhost
authorityKeyIdentifier=keyid,issuer
subjectKeyIdentifier=hash
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF
  openssl req -newkey rsa:2048 -nodes -subj "/CN=kube-apiserver" -keyout apiserver.key -out apiserver.csr 2>/dev/null
  openssl x509 -req -in apiserver.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
    -extfile san.ext -out apiserver.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=admin/O=system:masters" -keyout admin.key -out admin.csr 2>/dev/null
  openssl x509 -req -in admin.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 -out admin.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes \
    -subj "/CN=system:serviceaccount:flux-system:kustomize-controller/O=system:serviceaccounts/O=system:serviceaccounts:flux-system" \
    -keyout controller.key -out controller.csr 2>/dev/null
  openssl x509 -req -in controller.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
    -out controller.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=system:kube-controller-manager" \
    -keyout controllers.key -out controllers.csr 2>/dev/null
  openssl x509 -req -in controllers.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
    -out controllers.crt 2>/dev/null
  openssl genrsa -out sa.key 2048 2>/dev/null
  openssl rsa -in sa.key -pubout -out sa.pub 2>/dev/null
  cat >controller.kubeconfig <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: test
    cluster:
      server: https://apiserver:6443
      certificate-authority: /pki/ca.crt
users:
  - name: controller
    user:
      client-certificate: /pki/controller.crt
      client-key: /pki/controller.key
contexts:
  - name: test
    context: {cluster: test, user: controller}
current-context: test
EOF
  sed -e 's/name: controller$/name: controllers/' -e 's#/pki/controller\.#/pki/controllers.#g' \
    -e 's/user: controller}/user: controllers}/' controller.kubeconfig >controllers.kubeconfig
  chmod 0644 ./*
)

# --internal: no route out of the network, to the host or to a cluster.
apiserver_ip=""
for third in 240 241 242 243 244; do
  if docker network create --internal --subnet "172.30.${third}.0/24" "${id}" >/dev/null 2>&1; then
    apiserver_ip="172.30.${third}.10"
    break
  fi
done
[[ -n "${apiserver_ip}" ]] || { echo "no free subnet for the test network" >&2; exit 1; }

docker run -d --name "${id}-etcd" --network "${id}" --network-alias etcd "${etcd_image}" \
  etcd --data-dir=/tmp/etcd --listen-client-urls=http://0.0.0.0:2379 \
  --advertise-client-urls=http://etcd:2379 >/dev/null

docker run -d --name "${id}-apiserver" --network "${id}" --network-alias apiserver --ip "${apiserver_ip}" \
  -v "${pki}:/pki:ro" "registry.k8s.io/kube-apiserver:${version}" \
  kube-apiserver --etcd-servers=http://etcd:2379 --secure-port=6443 --bind-address=0.0.0.0 \
  --advertise-address="${apiserver_ip}" \
  --tls-cert-file=/pki/apiserver.crt --tls-private-key-file=/pki/apiserver.key \
  --client-ca-file=/pki/ca.crt --authorization-mode=RBAC \
  --service-account-issuer=https://kubernetes.default.svc \
  --service-account-key-file=/pki/sa.pub --service-account-signing-key-file=/pki/sa.key \
  --service-cluster-ip-range=10.96.0.0/16 --disable-admission-plugins=ServiceAccount >/dev/null

# The aggregated roles: admin is empty until this controller fills it. The
# workload controllers set the status of a Deployment or a StatefulSet with
# no replicas, which a wait reads; with no node, no pod runs. The
# controller manager stops when it starts before the API server is ready:
# Docker restarts it.
docker run -d --name "${id}-controllers" --network "${id}" --restart on-failure:30 \
  -v "${pki}:/pki:ro" "registry.k8s.io/kube-controller-manager:${version}" \
  kube-controller-manager --kubeconfig=/pki/controllers.kubeconfig \
  --controllers=clusterrole-aggregation,deployment,replicaset,statefulset --use-service-account-credentials=true \
  --service-account-private-key-file=/pki/sa.key --root-ca-file=/pki/ca.crt \
  --leader-elect=false >/dev/null

# The artifact of the test tree, as source-controller would serve it.
docker run -d --name "${id}-source" --network "${id}" --network-alias source --user 10001:10001 \
  --read-only -v "${work}/srv:/srv:ro" --entrypoint python3 "${python_image}" \
  -m http.server 8080 --bind 0.0.0.0 --directory /srv >/dev/null

tests() {
  docker run --rm --name "${id}-tests" --network "${id}" --user 10001:10001 --read-only --tmpfs /tmp \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -v "${pki}:/pki:ro" -v "${work}/in:/work:ro" -v "${here}:/tests:ro" \
    --entrypoint python3 "${python_image}" /tests/test_flux_lockdown.py "$@"
}

# The controller of git, with the arguments given. RUNTIME_NAMESPACE is
# what the Deployment of git sets from its namespace.
controller() {
  docker rm -f "${id}-controller" >/dev/null 2>&1 || true
  docker run -d --name "${id}-controller" --network "${id}" --read-only --tmpfs /tmp \
    -e KUBECONFIG=/pki/controller.kubeconfig -e RUNTIME_NAMESPACE=flux-system \
    -v "${pki}:/pki:ro" "${image}" "$@" >/dev/null
}

tests Setup
controller "${args[@]}"
tests NoFlag
controller "${args_flag[@]}"
tests Flag
