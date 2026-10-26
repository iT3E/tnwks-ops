# ---------------------------------------------------------------------------------------------------------------------
# CLOUDFLARE DYNAMIC DNS (terraform)
#
# GENERATED ONTO THE ROUTER BY TERRAFORM. Do not edit in WinBox; the next apply
# overwrites it. Source of truth is
# infrastructure/terraform/modules/mikrotik/templates/ddns-cloudflare.rsc
#
# Replaces the VyOS `cloudflare-ddns` Podman container. RouterOS has no
# Cloudflare DDNS provider, so this is a scheduled script against the Cloudflare
# API. It updates an existing A record by id and never creates one.
#
# This record is load-bearing: WireGuard client configs use it as their endpoint,
# so if this stops updating, remote VPN access breaks the next time the WAN IP
# changes. The logging below is deliberately noisy on failure for that reason.
# ---------------------------------------------------------------------------------------------------------------------

:local zone "${zone_id}"
:local record "${record_id}"
:local name "${record_name}"
:local token "${credential}"
:local wan "${wan_interface}"
:local ttl "${ttl}"

# Survives between runs, not across reboots. An empty value after boot simply
# forces one update, which is the behaviour we want.
:global ddnsLastAddress

:local leased ""
:do {
  :set leased [/ip dhcp-client get [find interface=$wan] address]
} on-error={
  :log warning ("ddns: no DHCP client on " . $wan . ", cannot update " . $name)
}

:if ([:len $leased] = 0) do={
  :log warning ("ddns: no WAN address on " . $wan . " yet, skipping " . $name)
} else={
  # The lease is "a.b.c.d/nn"; the API wants the bare address.
  :local address [:pick $leased 0 [:find $leased "/"]]

  :if ($address = $ddnsLastAddress) do={
    # Unchanged. Stay quiet so the log stays useful.
  } else={
    :local url ("https://api.cloudflare.com/client/v4/zones/" . $zone . "/dns_records/" . $record)
    :local payload ("{\"type\":\"A\",\"name\":\"" . $name . "\",\"content\":\"" . $address . \
      "\",\"ttl\":" . $ttl . ",\"proxied\":false}")

    :do {
      # An HTTP error status makes fetch throw (on-error below). Also check the
      # body's "success", so an unexpected 200 is not cached as done.
      :local result [/tool fetch http-method=put url=$url \
        http-header-field=("Authorization: Bearer " . $token . ",Content-Type: application/json") \
        http-data=$payload output=user as-value]
      :if (($result->"data") ~ "\"success\": *true") do={
        # Only remember it on success, so a failed update retries next interval.
        :set ddnsLastAddress $address
        :log info ("ddns: " . $name . " updated to " . $address)
      } else={
        :log error ("ddns: Cloudflare REJECTED update for " . $name . \
          " (" . $address . ") - check the API token; remote VPN access may break")
      }
    } on-error={
      :log error ("ddns: update FAILED for " . $name . \
        " (" . $address . ") - remote VPN access may break")
    }
  }
}
