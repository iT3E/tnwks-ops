provider "proxmox" {
  pm_tls_insecure = true
  # PM_API_TOKEN_SECRET
  # PM_API_TOKEN_ID
  pm_api_url      = "https://10.240.240.100:8006/api2/json"
  pm_debug        = true
  pm_parallel     = 25
}
