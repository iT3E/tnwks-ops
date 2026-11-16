#!/usr/bin/env bash
# The tests of the admission policies of the run jobs (../admissionpolicy.yaml).
#
#   kubernetes/apps/ai/agents/workflows/run-receiver/admission/tests/run.sh
#
# It starts a throwaway control plane in containers: etcd, kube-apiserver
# (the version of the clusters) and kube-controller-manager with the Job, TTL
# and garbage collector controllers, on a Docker network with no route out.
# It applies the namespace, ../app/rbac.yaml and the policies, and runs
# test_admission.py in the image of the run receiver that ../app/agents.yaml
# pins, so the Jobs and the patches come from the receiver's own code. No
# kubelet runs, so no pod starts.
#
# Nothing touches a real cluster: no kubeconfig of the workstation goes into
# a container, the test kubeconfig names the throwaway API server only, and
# the network has no route out. Each container, the network and the
# certificates go at the end.
#
# POLICY=<file> tests another policy file (for example the one of main, to
# see the new cases fail). KEEP=1 keeps the containers after a failure.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
admission="$(dirname "${here}")"
app="$(dirname "${admission}")/app"
policy="${POLICY:-${admission}/admissionpolicy.yaml}"
version="${KUBE_VERSION:-v1.37.2}"
etcd_image="${ETCD_IMAGE:-registry.k8s.io/etcd:3.6.6-0}"
# The image of the run receiver and the run job: the one that agents.yaml pins.
receiver_image="$(grep -m1 -oE 'ghcr[.]io/it3e/tnwks-workflow-agents:[^ ]+@sha256:[0-9a-f]{64}' "${app}/agents.yaml")"
kubectl_bin="$(command -v kubectl)"

id="admission-test-$$"
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
    docker logs --tail 30 "${id}-controllers" 2>&1 | sed 's/^/controllers: /' || true
  fi
  docker rm -f "${id}-etcd" "${id}-apiserver" "${id}-controllers" >/dev/null 2>&1 || true
  docker network rm "${id}" >/dev/null 2>&1 || true
  rm -rf "${work}"
  exit "${status}"
}
trap finish EXIT

# Throwaway certificates: a CA, the API server, two clients, the token key.
(
  cd "${pki}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=admission-test-ca" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout ca.key -out ca.crt 2>/dev/null
  # Python 3.13 checks X.509 strictly: the server certificate needs its key
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
  for who in admin controllers; do
    cat >"${who}.kubeconfig" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: test
    cluster:
      server: https://apiserver:6443
      certificate-authority: /pki/ca.crt
users:
  - name: ${who}
    user:
      client-certificate: /pki/${who}.crt
      client-key: /pki/${who}.key
contexts:
  - name: test
    context: {cluster: test, user: ${who}}
current-context: test
EOF
  done
  chmod 0644 ./*
)

# --internal: no route out of the network, to the host or to a cluster.
# The network has no default route, so the API server needs a fixed address
# to advertise, and a fixed address needs a subnet of our own: the first free
# one of a few (Docker refuses one that overlaps another network).
apiserver_ip=""
for third in 250 251 252 253 254; do
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
  --service-cluster-ip-range=10.96.0.0/16 >/dev/null

# It stops when it starts before the API server is ready: Docker restarts it.
docker run -d --name "${id}-controllers" --network "${id}" --restart on-failure:30 \
  -v "${pki}:/pki:ro" "registry.k8s.io/kube-controller-manager:${version}" \
  kube-controller-manager --kubeconfig=/pki/controllers.kubeconfig \
  --controllers=job-controller,ttl-after-finished-controller,garbage-collector-controller \
  --use-service-account-credentials=true \
  --service-account-private-key-file=/pki/sa.key --root-ca-file=/pki/ca.crt \
  --leader-elect=false >/dev/null

docker run --rm --network "${id}" --user 10001:10001 --read-only --tmpfs /tmp \
  -e HOME=/tmp -e KUBECONFIG=/pki/admin.kubeconfig -e PYTHONPATH=/app/src -e PYTHONDONTWRITEBYTECODE=1 \
  -v "${pki}:/pki:ro" -v "${kubectl_bin}:/usr/local/bin/kubectl:ro" \
  -v "${here}:/tests:ro" -v "${policy}:/work/admissionpolicy.yaml:ro" \
  -v "${app}/rbac.yaml:/work/rbac.yaml:ro" -v "${app}/agents.yaml:/work/agents.yaml:ro" \
  --entrypoint python3 "${receiver_image}" -m unittest discover -s /tests -t /tests -v
