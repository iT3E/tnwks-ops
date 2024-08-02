# Overview

Tried to run kubectl commands and received error:

```bash
E0801 20:51:19.474232 1349245 memcache.go:265] couldn't get current server API group list: the server has asked for the client to provide credentials
E0801 20:51:19.478411 1349245 memcache.go:265] couldn't get current server API group list: the server has asked for the client to provide credentials
E0801 20:51:19.482796 1349245 memcache.go:265] couldn't get current server API group list: the server has asked for the client to provide credentials
E0801 20:51:19.486653 1349245 memcache.go:265] couldn't get current server API group list: the server has asked for the client to provide credentials
E0801 20:51:19.519804 1349245 memcache.go:265] couldn't get current server API group list: the server has asked for the client to provide credentials
error: You must be logged in to the server (the server has asked for the client to provide credentials)
```

## Resolution

- Kubeconfig certificates were expired, found by running:
  ` cat kubeconfig | grep client-certificate-data | cut -f2 -d : | tr -d ' ' | base64 -d | openssl x509 -text -out -`

- SSH'd into one of the k8s masters and was able to run kubectl commands there. Ran `kubectl config view --raw > ~/.kube/config` to generate a new kubeconfig
