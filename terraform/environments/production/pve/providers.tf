provider "proxmox" {
  pm_tls_insecure     = true
  pm_api_token_id     = data.sops_file.secrets.data["pm_api_token_id"]
  pm_api_token_secret = data.sops_file.secrets.data["pm_api_token_secret"]
  pm_api_url          = data.sops_file.secrets.data["pm_api_url"]
  pm_debug            = true
  pm_parallel         = 25
}
