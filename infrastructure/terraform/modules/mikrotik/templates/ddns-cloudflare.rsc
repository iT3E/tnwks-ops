# ---------------------------------------------------------------------------------------------------------------------
# CLOUDFLARE DYNAMIC DNS (terraform)
#
# GENERATED ONTO THE ROUTER BY TERRAFORM. Do not edit in WinBox; the next apply
# overwrites it. Source of truth is
# infrastructure/terraform/modules/mikrotik/templates/ddns-cloudflare.rsc
#
# Replaces the VyOS `cloudflare-ddns` Podman container. RouterOS has no
# Cloudflare DDNS provider, so this is a scheduled script against the Cloudflare
# API. Unlike Namecheap this needs the zone id and the record id up front; the
# record must already exist. Create it in environments/prod/cloudflare, not here.
#
# If this record is a WireGuard endpoint, it is load-bearing: when it stops
# updating, remote VPN access breaks at the next WAN IP change.
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
      /tool fetch http-method=put url=$url \
        http-header-field=("Authorization: Bearer " . $token . ",Content-Type: application/json") \
        http-data=$payload keep-result=no
      # Only remember it on success, so a failed update retries next interval.
      :set ddnsLastAddress $address
      :log info ("ddns: " . $name . " updated to " . $address)
    } on-error={
      :log error ("ddns: update FAILED for " . $name . " (" . $address . ")")
    }
  }
}
