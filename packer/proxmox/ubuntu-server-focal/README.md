
Since neither Packer nor HCL in general have SOPS functionality, you must first run the below commands locally to get the secrets in environment variables.

```
export PROXMOX_API_URL=$(sops -d packer/proxmox/ubuntu-server-focal/secretvars.sops.yaml | yq eval '.proxmox_url' -)
export PROXMOX_API_TOKEN_ID=$(sops -d packer/proxmox/ubuntu-server-focal/secretvars.sops.yaml | yq eval '.proxmox_api_token_id' -)
export PROXMOX_API_TOKEN_SECRET=$(sops -d packer/proxmox/ubuntu-server-focal/secretvars.sops.yaml | yq eval '.proxmox_api_token_secret' -)
export PACKER_SSH_USERNAME=$(sops -d packer/proxmox/ubuntu-server-focal/secretvars.sops.yaml | yq eval '.packer_ssh_username' -)
export PACKER_SSH_PASSWORD=$(sops -d packer/proxmox/ubuntu-server-focal/secretvars.sops.yaml | yq eval '.packer_ssh_password' -)
```

the local web server to deploy the autoinstall file requires the below command if using secrets and SOPS.  Take care as this exports secrets to file 'user-data' locally
```
envsubst < tmpl/packer/autoinstall.template.yaml > packer/proxmox/ubuntu-server-focal/http/user-data
```
