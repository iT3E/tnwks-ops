resource "cloudflare_zone" "zone" {
  name    = var.domain
  account = { id = var.account_id }
  type    = "full"
  # v5 dropped `plan`; the zone stays on its existing (free) subscription.
}
