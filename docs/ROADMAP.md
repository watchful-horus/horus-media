# Roadmap

Status as of 2026-10-30: version 0.4.2. The original v1 scope is complete and the project is public (repository and Docker image). Per-version details are in [CHANGELOG.md](../CHANGELOG.md). `1.0.0` comes once `ct/horus.sh` has run on a real Proxmox host and the published image has had real use.

## Done

**Connections**
- Plex and Emby clients behind one `MediaServer` interface (`test_connection`, `get_sessions`, posters; reads only, apart from the admin-only `stop_session`)
- "Sign in with Plex" (plex.tv PIN flow: pick a server, no token hunting); manual token / API key entry still works
- Server tokens encrypted at rest (Fernet, key next to the database); never logged, never returned by the API
- Rename a server (everything follows, since history joins on the id)
- First admin from `HORUS_ADMIN_USER` + password file, so a container never shows an open first-run screen
- Getting-started guidance (welcome checklist, "import history" hint after adding a server)

**Activity**
- Live sessions from every server in one view, large cards with poster, quality, stream, container, video, audio, subtitle, location, bandwidth, progress and ETA
- Totals per server and overall, split into local and remote; amber Paused badge
- "Nothing playing" state per server; manual refresh and a choice of auto-refresh interval
- Stop stream (admin only, signed-in session only, logged; Plex needs Plex Pass). Tested with fake servers, and the author confirmed it works on a real Plex (2026-10-29); not tried on a real Emby

**History and graphs**
- Background poller records plays and bandwidth samples (`HORUS_POLL_SECONDS`, default 15); optional retention
- Graphs: plays over time (hour / day / week / month, up to 90 days for hours), direct play vs transcode over time, bandwidth over time (with combined Total), plays by user / platform / hour of day / weekday, media type
- Plays per month for the last 12 months, stacked by server; the Graphs page loads on demand
- Earlier/later arrows, presets and All time, "View as table" on every chart, validated colour palette, light and dark themes
- Import of past history (Plex history, Emby Playback Reporting plugin), safe to repeat

**Accounts and API**
- First-run account creation (the first person picks their username and becomes admin); admin and user roles; admins manage people; scrypt, sessions, rate-limited sign-in; an email address works as a username
- API keys (hashed, shown once), per-user keys, and a read-only `/api/v1` data API for Grafana: plays, bandwidth, history, activity, servers; JSON and CSV
- Control keys (off by default, always expiring, never able to touch users, keys, sign-in or Stop stream, every use logged) and `scripts/horusctl.py`
- Hide other people's activity from normal users (per-account Plex/Emby name link)
- Prometheus `/metrics`

**Operations**
- Settings: time zone, poll interval, retention, HTTP port, "allow other computers", Restart, Shut down (disabled in containers), update address and daily check with a time of day, backups
- Backups: manual and scheduled zips (with a time of day) and retention, token-free export, restore from the UI or an uploaded file (validates first, saves the current state first), delete backups
- Application logging: rotating file and console, secret scrubbing, level setting, Settings > Logs viewer
- Update check with install-specific update steps; an admin-only Update now button for a Proxmox container that can write to its own environment (works on a real container, confirmed 2026-10-29) (Docker and manual installs are never updated by Horus)
- Notification channels (Discord, ntfy, webhook), optional alerts, weekly/monthly summary reports with CSV. (A read-only Seerr requests tab was added and then removed on 2026-10-29: it had nothing to do with plays or stats)
- Auto / light / dark theme toggle; Windows "start at login"; `create-admin` and `configure` commands

**Installing and project**
- `Dockerfile`, `compose.example.yaml` and the image on ghcr.io (amd64), published by a tag workflow
- Proxmox installer wizard (`scripts/proxmox/horus-lxc.sh`), run on a real host; the first admin is created in the browser (or optionally during the install with `ADMIN_USER`/`ADMIN_PASSWORD`)
- Community-scripts style installer (`ct/horus.sh`, `install/horus-install.sh`, `json/horus.json`) and a release workflow that builds the web UI and wheel on a `v*` tag (its install steps ran in a throwaway container; `ct/horus.sh` itself ran on a real Proxmox host on 2026-10-29: 0.3.0 installed, first admin created in the browser, Plex added, history imported)
- MIT licence, `CONTRIBUTING.md`, `SECURITY.md`, issue and pull request templates, Dependabot, CODEOWNERS
- Public repository with secret scanning, private vulnerability reporting, CodeQL, Dependabot alerts, protected `main`, SHA-pinned Actions
- CI (tests, lint, Docker image, web build) on every push and pull request
- 257 tests (fake responses, no live servers), `ruff` clean; README and docs

**Verified**
- Backup Download then Restore across installs (Proxmox container to Docker); Emby history import; lint and the full suite on real Linux; the Docker image in CI and a real Docker host, including an anonymous pull; all tests on emulated aarch64

## Next

- [ ] Try Stop stream on a real Emby (never without the author's say-so)
- [ ] Try the new Docker admin variables (`HORUS_ADMIN_USER` and a password file) in a real container (Update now was confirmed on a real Proxmox container on 2026-10-29)
- [ ] Real-server test run by hand (a real Plex and Emby in a throwaway setup) to catch API changes the fakes can't
- [ ] arm64 Docker image (the app and its tests pass on aarch64 under emulation; the image build is untried)
- [ ] Not yet tried on real hardware: restoring a backup that is older than the current database version
- [ ] Mobile: pages laid out properly on a phone (a mobile web view), or a mobile app. To decide which: responsive web layout first, installable PWA next, native app only if those fall short
- [ ] More notification kinds
- [ ] GitHub Discussions
- [ ] Community-scripts submission is a long way off: a new app must be 6+ months old, actively maintained and have 600+ stars with release tarballs. The `ct/`, `install/` and `json/` files are kept in their format so it stays possible
- [ ] A `horus-media` console command (not `horus`: another package already installs that name)

**Later (not planned yet):** Jellyfin. (A watchlist view was dropped.)

**Tidy-ups**
- [ ] Switch tests to `httpx2` once FastAPI/Starlette require it (the warning is filtered for now)

## Known limits
- Emby history import uses the Playback Reporting plugin's `UserPlaylist` endpoint; field names were read from the plugin's source and imports have only been tried on one Emby server
- Imported plays carry no bandwidth, and Plex imports have no play method (shown as Unknown)
- "Play started" notifications also fire for streams already running when Horus starts
- Plays are only recorded while Horus is running; very short plays are counted too
- Plex servers shared with you usually do not expose live sessions to non-owners, so activity for them will probably not work
- Emby direct-play bandwidth is an estimate from the file's bitrate (Emby reports no live rate)
- Until the first account exists, anyone who can open the page can create it, unless `HORUS_ADMIN_USER` is set (Docker listens on the network from the start)
