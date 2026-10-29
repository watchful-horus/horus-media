# Changelog

Newest first. Each version is a tag on GitHub, with a Docker image and a release download.

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
