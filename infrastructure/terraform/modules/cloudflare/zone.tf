resource "cloudflare_zone" "zone" {
  zone       = var.domain
  account_id = var.account_id
  plan       = "free"
  type       = "full"
}
