#!/usr/bin/env bash
# The tests of the admission policies of NFS volumes (../nfsvolumes.yaml).
#
#   uv run --project observability kubernetes/apps/storage/nfs-server/admission/tests/run.sh
#
# It starts a throwaway control plane in containers: etcd, kube-apiserver
# (the version of the clusters) and kube-controller-manager with two
# controllers, the one that type-checks a policy and the binder of volumes
# and claims, on a Docker network with no route out. It runs
# test_nfs_volumes.py in a plain Python image on that network. The policies,
# the pods of today's users of the NFS server and the volumes and claims of
# the vault shares come from git, as JSON (render.py, in the Python of the
# repository, which has a YAML parser). No kubelet runs, so no pod starts:
# each pod is a dry run. The API server writes its audit log to its output;
# audit.py reads it at the end.
#
# Nothing touches a real cluster: no kubeconfig of the workstation goes into
# a container, the test talks to the throwaway API server only, and the
# network has no route out. Each container, the network and the
# certificates go at the end. KEEP=1 keeps them after a failure.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
version="${KUBE_VERSION:-v1.37.2}"
etcd_image="${ETCD_IMAGE:-registry.k8s.io/etcd:3.6.6-0}"
# The Python image of the checks of the agent platform.
python_image="docker.io/library/python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928"

id="nfs-admission-test-$$"
work="$(mktemp -d)"
pki="${work}/pki"
mkdir -p "${pki}" "${work}/in"

finish() {
  local status=$?
  if ((status != 0)) && [[ "${KEEP:-}" == "1" ]]; then
    echo "kept: containers ${id}-*, network ${id}, files ${work}"
    exit "${status}"
  fi
  if ((status != 0)); then
    docker logs --tail 30 "${id}-apiserver" 2>&1 | sed 's/^/apiserver: /' || true
    docker logs --tail 30 "${id}-controllers" 2>&1 | sed 's/^/controllers: /' || true
  fi
  docker rm -f "${id}-etcd" "${id}-apiserver" "${id}-controllers" >/dev/null 2>&1 || true
  docker network rm "${id}" >/dev/null 2>&1 || true
  rm -rf "${work}"
  exit "${status}"
}
trap finish EXIT

"${PYTHON:-python3}" "${here}/render.py" "${work}/in"

# Throwaway certificates: a CA, the API server, two clients, the token key.
(
  cd "${pki}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=nfs-admission-test-ca" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout ca.key -out ca.crt 2>/dev/null
  # Python checks X.509 strictly: the server certificate needs its key
  # identifiers and usages.
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
  openssl req -newkey rsa:2048 -nodes -subj "/CN=system:kube-controller-manager" \
    -keyout controllers.key -out controllers.csr 2>/dev/null
  openssl x509 -req -in controllers.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 1 \
    -out controllers.crt 2>/dev/null
  openssl genrsa -out sa.key 2048 2>/dev/null
  openssl rsa -in sa.key -pubout -out sa.pub 2>/dev/null
  cat >controllers.kubeconfig <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: test
    cluster:
      server: https://apiserver:6443
      certificate-authority: /pki/ca.crt
users:
  - name: controllers
    user:
      client-certificate: /pki/controllers.crt
      client-key: /pki/controllers.key
contexts:
  - name: test
    context: {cluster: test, user: controllers}
current-context: test
EOF
  # The audit log: the metadata of each change of a pod or a volume, which
  # holds the audit annotations of the admission policies.
  cat >audit-policy.yaml <<'EOF'
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: ["RequestReceived"]
rules:
  - level: Metadata
    verbs: ["create", "update", "patch"]
    resources:
      - group: ""
        resources: ["pods", "persistentvolumes", "persistentvolumeclaims"]
  - level: None
EOF
  chmod 0644 ./*
)

# --internal: no route out of the network, to the host or to a cluster. The
# API server needs a fixed address to advertise, and so a subnet of our own:
# the first free one of a few.
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
  --service-cluster-ip-range=10.96.0.0/16 \
  --audit-policy-file=/pki/audit-policy.yaml --audit-log-path=- >/dev/null

# The type check of a policy fills its status (status.typeChecking): the
# test reads it. The binder binds a claim to its volume, as in the cluster,
# with no pod. It stops when it starts before the API server is ready:
# Docker restarts it.
docker run -d --name "${id}-controllers" --network "${id}" --restart on-failure:30 \
  -v "${pki}:/pki:ro" "registry.k8s.io/kube-controller-manager:${version}" \
  kube-controller-manager --kubeconfig=/pki/controllers.kubeconfig \
  --controllers=validatingadmissionpolicy-status-controller,persistentvolume-binder-controller \
  --use-service-account-credentials=true \
  --service-account-private-key-file=/pki/sa.key --root-ca-file=/pki/ca.crt \
  --leader-elect=false >/dev/null

docker run --rm --network "${id}" --user 10001:10001 --read-only --tmpfs /tmp \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "${pki}:/pki:ro" -v "${work}/in:/work:ro" -v "${here}:/tests:ro" \
  --entrypoint python3 "${python_image}" /tests/test_nfs_volumes.py

# The audit annotations: the volumes and claims of git have none, and a
# volume off the list (the control of the test) has one.
docker logs "${id}-apiserver" 2>/dev/null | "${PYTHON:-python3}" "${here}/audit.py" "${work}/in"
