## ---------------------------------------------------------------------------------------------------------------------
## CHECKS
## One simple (timeout + grace) check per entry in var.checks, all notifying
## the integration from data.tf.
## ---------------------------------------------------------------------------------------------------------------------

resource "healthchecksio_check" "this" {
  for_each = var.checks

  name    = each.key
  desc    = each.value.desc
  tags    = each.value.tags
  timeout = each.value.timeout
  grace   = each.value.grace

  channels = [data.healthchecksio_channel.this.id]
}
