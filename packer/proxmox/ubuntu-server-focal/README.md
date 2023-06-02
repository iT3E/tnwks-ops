
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

A port forward rule is configured on PFSense to forward 8802 traffic destined for 172.22.0.0/16 to the windows PC.

I have used [Wsl-IpHandler](https://github.com/wikiped/Wsl-IpHandler) to set a static IP on the WSL instance.  Currently using the 'dynamic' mode.

Run below command in Powershell to open Windows PC firewall
```
netsh advfirewall firewall add rule name="Packer HTTP Server" dir=in action=allow protocol=TCP localport=8802
```

to open a port proxy on Windows PC
```
netsh interface portproxy add v4tov4 listenport=8802 listenaddress=0.0.0.0 connectport=8802 connectaddress=172.22.96.2
```
