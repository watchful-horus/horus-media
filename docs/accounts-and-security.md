# Accounts and security

## The first account
The first time you open Horus it asks you to choose a **username** and password. That first account is an admin. Until it exists, nothing else works.

**Create it as soon as Horus starts.** Until the first account exists, anyone who can open the page could create it, so do this before you share the address with anyone, and never put an unconfigured Horus on the internet.

- A normal install listens only on the computer it runs on until an admin exists.
- Docker and containers listen on the network from the start, so either create the account straight away or set `HORUS_ADMIN_USER` (see below) so it exists before anyone can open the page.
- The Proxmox installers (like Docker) open the first-run screen on the network and print the address: open it and create the account straight away. Optionally they can create the admin during the install instead (`ADMIN_USER` and `ADMIN_PASSWORD` for the wizard).

### Creating the admin from the environment (Docker)
Set `HORUS_ADMIN_USER` and either `HORUS_ADMIN_PASSWORD_FILE` (a file whose first line is the password; preferred, since it stays out of `docker inspect`) or `HORUS_ADMIN_PASSWORD`. When Horus starts and no admin exists yet, it creates that account, so the first-run screen never appears. Once any admin exists the variables are ignored, so changing the password there later changes nothing and you can remove them. A password that is too short, or a file that can't be read, is logged (never the password) and leaves the normal first-run screen in place. In `compose.example.yaml` the lines are there to uncomment; the container runs as user 10001, so the password file must be readable by it.

After that every page and API call needs a sign-in (a browser session) or, for the data API only, an API key.

## Roles
There are two roles. Admins add people in **Settings > Users** and choose each person's role.

| | Admin | User |
|---|---|---|
| Activity and Graphs | yes | yes |
| Change own password, username and display name, sign out their other browsers, and create own API keys (Account) | yes | yes |
| See the IP address a stream is played from | yes | no (only "LAN" or "WAN") |
| Set own Plex/Emby/Jellyfin name | yes | no (an admin sets it) |
| Edit anyone's details (click their name under Settings > Users) | yes | no |
| Servers, settings, backups, logs, restart | yes | no |
| Add or remove people, see and revoke everyone's API keys, control keys | yes | no |
| Stop a stream | yes (signed in) | no |

Usernames are 3 to 64 letters, numbers, dots, dashes, underscores, `@` or `+`, so an email address works, and they are not case-sensitive. Usernames show up in the log (for example "login: name from address"), so treat a shared log as personal data. There is always at least one admin: the last one cannot be deleted or demoted, and you cannot delete or demote yourself. Anything added to Horus later is admin-only until it is deliberately opened to users.

**Your details.** Your name in the top bar opens a small menu: **Account**, the theme (auto, light or dark) and **Sign out**. On the Account page you can change your username (your other browsers stay signed in), set an optional display name that the top bar and the Users list show instead of the username, and change your password. Admins can also set their own Plex/Emby/Jellyfin name there. Admins can open anyone's details by clicking their name under **Settings > Users**: username, display name, Plex/Emby/Jellyfin name, role, password reset, sign out everywhere and delete. A normal user can't set a Plex/Emby/Jellyfin name, because with privacy on it decides whose plays they can see. Control keys can't change any of this.

## What is protected
- Passwords are stored as scrypt hashes (about 32 MB of memory per sign-in, one of the settings OWASP lists; a hash made by an older version is renewed the next time that person signs in) and can be up to 1024 characters. At most two passwords are checked at the same moment, so a burst of sign-in attempts cannot use up the memory of a small container. Session tokens and API keys only as SHA-256 hashes. Repeated wrong passwords are slowed down: after five in a minute for one username from one address, that username has to wait a minute from that address (other people there, and the same person elsewhere, are not affected), and an address that fails thirty times in a minute across any usernames has to wait too. Sign-in errors do not say whether the username or the password was wrong. The change-password form counts the same way per account: five wrong entries of the current password in a minute, then a minute's wait.
- Changing or resetting a password signs that person out everywhere else. A sign-in lasts 30 days per browser; **Sign out everywhere else** on the Account page ends your other ones without changing the password, and an admin can do the same for a person under Settings > Users (a lost phone, say).
- On Linux the database file is readable only by the account Horus runs as, and so is the folder it lives in (the key, backups and log are there too). If another account needs to read that folder, for a backup job say, run that job as the same account or as root.
- Server tokens are encrypted at rest, never logged, and never returned by the API.
- Changes are accepted only from Horus's own pages. A request that adds, changes or deletes something is refused when the browser says it was sent by a page at another address, including another web app on the same machine at a different port. Scripts and API keys are not affected (they are not web pages).
- Every response carries a content security policy (scripts, styles and requests only from Horus itself), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- Horus cannot be shown inside another page (a frame), so it cannot be embedded in a dashboard such as Homarr or Organizr; link to it instead.
- Posters are passed on only as JPEG, PNG, WebP or GIF, and the browser may show them as pictures and nothing else.
- Upgrading from the earlier single-admin version keeps your password: that account becomes the admin, with the username `admin`.
- `HORUS_AUTH_DISABLED=1` turns the login off, for people who put their own sign-in in front of Horus. There are no accounts then, and anyone who can reach Horus can use it. See [Without a login](#without-a-login) for the one check that remains.

## Privacy between users
Users on a normal account can see everyone's Activity and Graphs, including other people's names and what they watched. Only give accounts to people you are happy to show that.

To limit this, turn on *Normal users see only their own activity* under **Settings > Users**. Normal users (and their API keys) then see only plays by one Plex/Emby/Jellyfin name. Link that name to each account under **Settings > Users**. Without a linked name they see nothing. Admins always see everything. Server-wide bandwidth is hidden from limited users, because it would reveal what others are watching.

Whatever that setting says, only admins see the IP address a stream is played from. A normal user, and a key that belongs to one, sees "LAN" or "WAN" in its place.

## API keys and control keys
Every signed-in person can create their own API keys under **Account**; a key acts as its owner and can only use the read-only data API. A key lasts until it is revoked unless you give it an end date (30, 90 or 365 days) when you create it; after that date it stops working and the list shows it as expired. Admins can also see and revoke every key under **Settings > API**.

Control keys are a special kind of key for testing and automation. See [API](api.md#control-keys-for-testing-and-automation).

## The one write action
Horus reads from Plex, Emby and Jellyfin and changes nothing on them, with one exception: an admin can press **Stop stream** on the Activity page to end a playing stream. It works only from a signed-in admin session, never with an API key or control key, it is logged by name, and Plex needs Plex Pass for it.

## Without a login
While Horus answers without a login (no account exists yet, or `HORUS_AUTH_DISABLED` is set), it answers only when it is opened by its IP address, by `localhost`, or by a name listed in [`HORUS_ALLOWED_HOSTS`](configuration.md#environment-variables). Any other name gets a short page that says so. This stops a web page on another site from pointing a domain name of its own at your Horus and using it through your browser ("DNS rebinding"). Once an account exists and the login is on, any name works and nothing needs to be set.

## Addresses an admin types in
An admin can point Horus at any address: a media server, a webhook, the release address. Horus will call whatever is entered, including other services on your network, so only give the admin role to people you would let do that. A media server added with an `http://` address gets its token sent unencrypted on every request; that is normal on a home network, but use `https://` when the server is somewhere else.

## Exposing Horus
Horus is meant for a home network. If you make it reachable from the internet, put it behind a reverse proxy with HTTPS, use strong passwords and keep Horus up to date. The proxy should pass the `Host` header on unchanged and set `X-Forwarded-Proto` (the usual defaults); otherwise an old browser may be told "This request did not come from Horus itself" when saving. With `X-Forwarded-Proto: https` the session cookie is marked `Secure` by itself. If the proxy runs on another machine or in another container, also set [`HORUS_TRUSTED_PROXIES`](configuration.md#environment-variables) so sign-ins are logged and limited by each visitor's own address rather than the proxy's. To report a problem privately, see [SECURITY.md](../SECURITY.md).
