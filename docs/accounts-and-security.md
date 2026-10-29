# Accounts and security

## The first account
The first time you open Horus it asks you to choose a **username** and password. That first account is an admin. Until it exists, nothing else works.

**Create it as soon as Horus starts.** Until the first account exists, anyone who can open the page could create it, so do this before you share the address with anyone, and never put an unconfigured Horus on the internet.

- A normal install listens only on the computer it runs on until an admin exists.
- Docker and containers listen on the network from the start, so either create the account straight away or set `HORUS_ADMIN_USER` (see below) so it exists before anyone can open the page.
- The Proxmox installer avoids the problem by creating the admin during the install.

### Creating the admin from the environment (Docker)
Set `HORUS_ADMIN_USER` and either `HORUS_ADMIN_PASSWORD_FILE` (a file whose first line is the password; preferred, since it stays out of `docker inspect`) or `HORUS_ADMIN_PASSWORD`. When Horus starts and no admin exists yet, it creates that account, so the first-run screen never appears. Once any admin exists the variables are ignored, so changing the password there later changes nothing and you can remove them. A password that is too short, or a file that can't be read, is logged (never the password) and leaves the normal first-run screen in place. In `compose.example.yaml` the lines are there to uncomment; the container runs as user 10001, so the password file must be readable by it.

After that every page and API call needs a sign-in (a browser session) or, for the data API only, an API key.

## Roles
There are two roles. Admins add people in **Settings > Users** and choose each person's role.

| | Admin | User |
|---|---|---|
| Activity and Graphs | yes | yes |
| Change own password and create own API keys (Account) | yes | yes |
| Servers, settings, backups, logs, restart | yes | no |
| Add or remove people, see and revoke everyone's API keys, control keys | yes | no |
| Stop a stream | yes (signed in) | no |

Usernames are 3 to 64 letters, numbers, dots, dashes, underscores, `@` or `+`, so an email address works, and they are not case-sensitive. Usernames show up in the log (for example "login: name from address"), so treat a shared log as personal data. There is always at least one admin: the last one cannot be deleted or demoted, and you cannot delete or demote yourself. Anything added to Horus later is admin-only until it is deliberately opened to users.

## What is protected
- Passwords are stored as scrypt hashes; session tokens and API keys only as SHA-256 hashes. Repeated wrong passwords are slowed down, and sign-in errors do not say whether the username or the password was wrong.
- Changing or resetting a password signs that person out everywhere else.
- Server tokens are encrypted at rest, never logged, and never returned by the API.
- Upgrading from the earlier single-admin version keeps your password: that account becomes the admin, with the username `admin`.
- `HORUS_AUTH_DISABLED=1` turns the login off, for people who put their own sign-in in front of Horus. There are no accounts then, and anyone who can reach Horus can use it.

## Privacy between users
Users on a normal account can see everyone's Activity and Graphs, including other people's names and what they watched. Only give accounts to people you are happy to show that.

To limit this, turn on *Normal users see only their own activity* under **Settings > General**. Normal users (and their API keys) then see only plays by one Plex/Emby name. Link that name to each account under **Settings > Users**. Without a linked name they see nothing. Admins always see everything. Server-wide bandwidth is hidden from limited users, because it would reveal what others are watching.

## API keys and control keys
Every signed-in person can create their own API keys under **Account**; a key acts as its owner and can only use the read-only data API. Admins can also see and revoke every key under **Settings > API**.

Control keys are a special kind of key for testing and automation. See [API](api.md#control-keys-for-testing-and-automation).

## The one write action
Horus reads from Plex and Emby and changes nothing on them, with one exception: an admin can press **Stop stream** on the Activity page to end a playing stream. It works only from a signed-in admin session, never with an API key or control key, it is logged by name, and Plex needs Plex Pass for it.

## Exposing Horus
Horus is meant for a home network. If you make it reachable from the internet, put it behind a reverse proxy with HTTPS, use strong passwords and keep Horus up to date. To report a problem privately, see [SECURITY.md](../SECURITY.md).
