provider "cloudflare" {
  access_key  = data.sops_file.secrets.data["cloudflare_email"]
  secret_key  = data.sops_file.secrets.data["cloudflare_api_key"]
}
