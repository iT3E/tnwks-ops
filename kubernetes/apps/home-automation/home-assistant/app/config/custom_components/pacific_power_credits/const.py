"""Constants for Pacific Power solar credit balances."""

DOMAIN = "pacific_power_credits"
CONF_MODE = "mode"
CONF_SNAPSHOT = "snapshot"
CONF_CUSTOMER_ID = "customer_id"
CONF_ACCOUNT_SEQUENCE = "account_sequence"
MODE_BILL = "bill"
MODE_PORTAL = "portal"

# Used by the MIT-licensed portal authentication implementation.
UTILITY_PACIFIC_POWER = "pacific_power"
UTILITY_DOMAINS = {
    UTILITY_PACIFIC_POWER: {
        "base_url": "https://csapps.pacificpower.net",
        "login_url": "https://login.csapps.pacificpower.net",
        "subsidiary": "PacificPower",
        "policy": "B2C_1A_PAC_SIGNIN",
    }
}
