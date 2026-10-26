# Register-TnwksUdpRelays.ps1
#
# The UDP relays this Windows host runs, in one place (`task wsl-host:udp-relays`).
# Each is a Scheduled Task running Start-TnwksUdpRelay.ps1 on one port; see
# Register-TnwksUdpRelayTask.ps1. Run elevated. Re-running is safe.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$register = Join-Path $here 'Register-TnwksUdpRelayTask.ps1'

# NetFlow v9 from the router -> monitoring/flow-collector. From the LAN.
& $register -Port 2055 -TaskName 'tnwks-udp-relay'

# Router syslog -> monitoring/vector-aggregator. Only the router (its 910
# address) sends syslog.
& $register -Port 6001 -TaskName 'tnwks-udp-relay-syslog' -RemoteAddress '10.10.91.1'
