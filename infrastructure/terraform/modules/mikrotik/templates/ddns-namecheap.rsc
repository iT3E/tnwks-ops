# ---------------------------------------------------------------------------------------------------------------------
# NAMECHEAP DYNAMIC DNS (terraform)
#
# GENERATED ONTO THE ROUTER BY TERRAFORM. Do not edit in WinBox; the next apply
# overwrites it. Source of truth is
# infrastructure/terraform/modules/mikrotik/templates/ddns-namecheap.rsc
#
# Replaces the EdgeRouter Lite's `service dns dynamic interface eth0 service
# namecheap`. RouterOS has no Namecheap DDNS provider, so this is a scheduled
# script hitting Namecheap's update endpoint.
#
# This record is load-bearing: WireGuard client configs use it as their endpoint,
# so if this stops updating, remote VPN access breaks the next time the WAN IP
# changes. The logging below is deliberately noisy on failure for that reason.
# ---------------------------------------------------------------------------------------------------------------------

:local host "${host}"
:local domain "${domain}"
:local password "${credential}"
:local wan "${wan_interface}"

# Survives between runs, not across reboots. An empty value after boot simply
# forces one update, which is the behaviour we want.
:global ddnsLastAddress

:local leased ""
:do {
  :set leased [/ip dhcp-client get [find interface=$wan] address]
} on-error={
  :log warning ("ddns: no DHCP client on " . $wan . ", cannot update " . $host . "." . $domain)
}

:if ([:len $leased] = 0) do={
  :log warning ("ddns: no WAN address on " . $wan . " yet, skipping " . $host . "." . $domain)
} else={
  # The lease is "a.b.c.d/nn"; Namecheap wants the bare address.
  :local address [:pick $leased 0 [:find $leased "/"]]

  :if ($address = $ddnsLastAddress) do={
    # Unchanged. Stay quiet so the log stays useful.
  } else={
    :local url ("https://dynamicdns.park-your-domain.com/update\?host=" . $host . \
      "&domain=" . $domain . "&password=" . $password . "&ip=" . $address)

    :do {
      /tool fetch url=$url keep-result=no
      # Only remember it on success, so a failed update retries next interval.
      :set ddnsLastAddress $address
      :log info ("ddns: " . $host . "." . $domain . " updated to " . $address)
    } on-error={
      :log error ("ddns: update FAILED for " . $host . "." . $domain . \
        " (" . $address . ") - remote VPN access may break")
    }
  }
}
