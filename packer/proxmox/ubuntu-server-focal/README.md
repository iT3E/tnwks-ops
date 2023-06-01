
Since neither Packer nor HCL in general have SOPS functionality, you must first run the below commands locally to get the secrets in environment variables.

```
export PROXMOX_API_URL=$(sops -d secrets.sops.yaml | yq eval '.proxmox_url' -)
export PROXMOX_API_TOKEN_ID=$(sops -d secrets.sops.yaml | yq eval '.proxmox_api_token_id' -)
export PROXMOX_API_TOKEN_SECRET=$(sops -d secrets.sops.yaml | yq eval '.proxmox_api_token_secret' -)
```
