# Security

## Supported versions
Only the latest release gets security fixes.

## Reporting a vulnerability
Please **don't** open a public issue for a security problem. Use GitHub's private report instead:
open the **Security** tab of this repository and choose **Report a vulnerability**. Include:

- what you found and why it matters,
- the steps to reproduce it,
- the Horus version and how you installed it (Docker, Proxmox script, from source).

Horus is a hobby project. Reports are read and taken seriously, but there is no guaranteed
response time. Please give a reasonable amount of time to fix a problem before you share it
publicly.

## What counts
Things that matter most, because Horus stores credentials for people's media servers:

- a way to read, steal or log the stored Plex or Emby tokens or the encryption key,
- signing in, or reaching admin features, without the right account,
- reading or writing files outside Horus's data folder (for example through backup upload,
  restore or log download),
- a way to make Horus change anything on a Plex or Emby server (it only reads, except the admin-only
  "Stop stream" button, which no API key or control key may use),
- a way around the limits of control keys: using one when control keys are switched off or the key
  has expired, or using one to manage users or keys, change a password or sign in.

## What doesn't
- Problems in Plex, Emby or other software that Horus talks to.
- Issues that need an admin account to start with, or physical access to the machine.
- Missing hardening on a setup the README advises against.

## A note on exposure
Horus is meant for a home network. If you make it reachable from the internet, put it behind a
reverse proxy with HTTPS, use strong passwords and keep Horus up to date.
