## ---------------------------------------------------------------------------------------------------------------------
## SITE SETTINGS
## ---------------------------------------------------------------------------------------------------------------------

resource "unifi_setting" "site" {
  country = {
    code = var.country_code
  }

  mgmt = {
    auto_upgrade = {
      enabled = var.auto_upgrade
    }

    # With these credentials an orphaned device can be re-pointed over SSH
    # (`set-inform`) instead of factory-reset. Losing them along with the
    # controller database is what forced the 2026 reset.
    ssh = {
      enabled               = true
      auth_password_enabled = true
      username              = var.device_ssh.username
      password              = var.device_ssh.password
      keys                  = var.device_ssh.keys
    }
  }
}
