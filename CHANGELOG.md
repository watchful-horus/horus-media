# Changelog

Newest first. Each version is a tag on GitHub, with a Docker image and a release download.

## 0.4.2 (2026-10-30)

**Graphs**
- The Total line on the time charts is now thinner and dashed, so it no longer hides the per-server lines.

**Phones**
- On a phone-width screen the top bar wraps instead of cutting off Settings, the Settings section list scrolls sideways instead of stretching the page, and each server on Settings > Servers stacks its text above its buttons. Screens wider than 720 px look exactly as before.

## 0.4.1 (2026-10-30)

**Activity**
- TV episodes show their season and episode (for example "S1:E1") under the title on the live card, for Plex and Emby. The activity data API has a matching `episode` column.

**Documentation**
- The README now opens with a one-line pitch, a large screenshot and the `docker run` command, ahead of the feature list.

## 0.4.0 (2026-10-29)

**Settings and people**
- **Settings is easier to scan:** the long General page is split into four entries in the left list: General, Alerts and reports, Network and Maintenance (backups, updates, version, restart). They share one Save button. Old bookmarks such as `#settings/general/network` still work.
- **Click a name to see and change a person's details.** Under Settings > Users, a person's panel holds username, display name, Plex/Emby name, role, password reset and delete. The Users list has cleaner rows, and the "Normal users see only their own activity" setting now lives there instead of in General.
- **Your details:** the Account page has a new **Your details** card to change your own username and an optional display name (shown in the top bar and the Users list). Only admins set a Plex/Emby name; control keys can't change any of this.
- **Backups list:** Download, Restore and Delete line up in a column on the right.

**Docker**
- `HORUS_ALLOW_SELF_RESTART=1` gives a container a Restart button (Horus restarts inside the container; Shut down and the port stay with Docker). The page now explains in plain words why those controls are off by default, and the restart page-reload uses the port in your address bar.

**Activity and logs**
- The Total box is now the first one in the Activity totals strip.
- When a server is unreachable the log says why (timed out, connection refused or host not reachable, connection dropped) and, when it comes back, how long it was down.

**Removed**
- **The Seerr requests tab and its Integrations section.** It only listed Overseerr/Jellyseerr requests and was not connected to plays or stats. Any stored Seerr address and API key are deleted when you upgrade.

**Upgrading**
- Two database changes run automatically at start (migration 10 drops the Seerr connection, 11 adds the display name). A backup made after this version can't be restored into 0.3.x or older.

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
