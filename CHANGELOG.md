# Changelog

Newest first. Each version is a tag on GitHub, with a Docker image and a release download.

## Unreleased
- **Settings is easier to scan:** the long General page is split into four entries in the left list: General, Alerts and reports, Network and Maintenance (backups, updates, version, restart). They share one Save button. Old bookmarks such as `#settings/general/network` still work.

## 0.3.0 (2026-10-29)
- **First admin from the environment (Docker):** `HORUS_ADMIN_USER` with `HORUS_ADMIN_PASSWORD_FILE` (or `HORUS_ADMIN_PASSWORD`) creates the first admin when the container starts, so there is no open first-run screen on the network. Ignored once an admin exists.
- **Proxmox installers:** they no longer create an admin or generate a password. You open the printed address and create the first account yourself, with your own username and password (`HORUS_OPEN_FIRST_RUN=1` in the service lets Horus listen on the network until then; needs this version or newer). The wizard's optional `ADMIN_USER` / `ADMIN_PASSWORD` still create it during the install.
- **Update now (Proxmox container only):** an admin-only button (signed-in session; never an API or control key) that backs up, installs the newest release from GitHub and restarts. Shown only when the container can write to its own environment; `horus-lxc.sh` now sets that up. Docker and manual installs still show copy-and-run steps.
- **Getting started:** a welcome checklist on an empty Activity page, a "what now" hint with an Import history button right after adding a server, and clearer empty-Graphs wording.
- **Graphs:** a "Plays per month, last 12 months" chart stacked by server; the Graphs page and its chart library load only when opened (first load is about a fifth of the size).
- **Emby import:** a play that has only a date is placed at midday in your time zone instead of midnight UTC. Plays imported earlier are not duplicated.
- README: a `docker run` one-liner. Dependabot updates are grouped into one pull request per kind. A weekly check pulls the published image and verifies it starts healthy.

## 0.2.0 (2026-10-29)
- **Stop stream:** an admin can end a playing stream from the Activity page (Plex needs Plex Pass). Signed-in admin sessions only, never an API key or control key, logged by name. Tested with fake servers only.
- Amber **Paused** badge on paused streams.
- Activity totals per server and overall, split into local and remote; paused streams add no bandwidth.

## 0.1.4 (2026-10-29)
- Settings shows how to update for the way Horus runs (Docker, Proxmox container or manual install), with Copy buttons. Horus still never updates itself.

## 0.1.3 (2026-10-29)
- Rename a server; optional alerts (transcodes, bandwidth, new person); weekly and monthly summary reports.
- Network settings fix for containers; update check on by default with a time of day; time of day for automatic backups.
- Backup path hardening; redrawn graphs (solid lines, stacked weekday bars, calmer bandwidth).
- arm64 marked supported in the Proxmox script; GitHub Actions pinned to commit SHAs. The repository became public.

## 0.1.2 (2026-10-28)
- Control keys: API keys that can run Horus for testing and automation, off by default, always expiring, never able to touch users, keys or sign-in.

## 0.1.1 (2026-10-28)
- An email address works as a username; the remote first-run setup code was removed; newer build tools.

## 0.1.0 (2026-10-28)
- First tagged release: Plex and Emby connections, live activity, recorded history and graphs, accounts, API keys, backups, logs, Docker and Proxmox installers.
