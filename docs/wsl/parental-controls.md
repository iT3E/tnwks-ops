# Parental controls on a cluster host

WSL-02 runs homelab-wsl-02 and is also a desktop the kids use. The router
sees one address (10.10.91.112) for their browsing and for the cluster, so a
router rule or a family resolver on that address would hit the node too: a
public filtering resolver does not know the `tnwks.local` names that failover
and the site leases need. The controls live in the kids' Windows accounts
instead. Router-level filtering (a kids VLAN with its own resolver) is for a
device of their own.

## What Ansible keeps

Role `windows_parental_controls` (tag `parental-controls`), for the accounts
named in `parental_accounts` (encrypted, `host_vars/<host>/secrets.sops.yml`;
empty skips the role):

- **Standard user.** A member of Users. Membership of Administrators is
  removed; any other local group stops the run with no change.
- **Browser policies in the account's own hive** (its HKCU, written while it
  is signed in or through its `NTUSER.DAT` while it is not). No other
  account's browser changes. The user can read `HKCU\Software\Policies` but
  not write it. Chrome and Edge get the same set:
  - DNS-over-HTTPS in `secure` mode (no fallback) to `parental_doh_template`:
    Cloudflare for Families by default, or a NextDNS profile URL in the host's
    secrets. DoH goes straight from the browser, so the router and the
    cluster's DNS see nothing new.
  - Google and Bing SafeSearch, YouTube Restricted Mode (strict,
    `parental_youtube_restrict`).
  - No incognito/InPrivate, no guest mode, no new profiles, no extensions.
- **No shutting down the host from the account.** Start menu policy
  `NoClose` in its hive. Host-wide, `SeShutdownPrivilege` is held by
  Administrators and Backup Operators only (Users removed from the Windows
  default), and the sign-in screen has no power button
  (`shutdownwithoutlogon` 0). Every non-admin account loses Shut down and
  Restart through these. An admin shuts down or restarts with an elevated
  `shutdown /r /t 0`, and Ansible's reboots (patching,
  [windows-patching.md](../operations/windows-patching.md)) run as the admin account. Sleep is already
  off on WSL-02 (`windows_power`). Holding the power button still cuts power.

The role prints no account names: the Actions logs are public. Each run
reports, per account index, `no-account` (not made yet), `no-profile` (never
signed in) or the values it set.

## Owner steps for a new account

1. On [family.microsoft.com](https://family.microsoft.com), add the child to
   the family group (a child Microsoft account).
2. On the host, as an admin: Settings > Accounts > Family > Add someone, with
   that account. It is made as a standard user. Sign in with it once, so it
   has a profile.
3. In Family Safety: screen time, app and game limits, content age rating.
   Leave **Filter inappropriate websites** off. It filters Edge only and
   blocks Chrome and every other browser; the resolver and the policies above
   already filter both browsers.
4. Add the local account name (`Get-LocalUser`, often the first five letters
   of the email) to `parental_accounts` in `host_vars/<host>/secrets.sops.yml`,
   and optionally `parental_doh_template`. Merging applies it.
5. Check as the child: `chrome://policy` lists the policies as `Platform` /
   `Current user`, and [1.1.1.1/help](https://1.1.1.1/help) shows DoH on
   (Cloudflare) or [test.nextdns.io](https://test.nextdns.io) shows the
   profile (NextDNS).

## Not covered

Game clients have their own browsers and chat (the Steam overlay browser,
Steam chat, in-game chat). Use each one's parental settings, for Steam its
Family View.
