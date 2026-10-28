# Roadmap

Status as of 2026-10-28. The app is feature-complete for the original v1 scope and is being prepared for a public release.

## Done

**Connections**
- Plex and Emby clients behind one read-only `MediaServer` interface (`test_connection`, `get_sessions`, posters)
- "Sign in with Plex" (plex.tv PIN flow: pick a server, no token hunting); manual token / API key entry still works
- Server tokens encrypted at rest (Fernet, key next to the database); never logged, never returned by the API

**Activity**
- Live sessions from every server in one view, large cards with poster, quality, stream, container, video, audio, subtitle, location, bandwidth, progress and ETA
- "Nothing playing" state per server; manual refresh and a choice of auto-refresh interval

**History and graphs**
- Background poller records plays and bandwidth samples (`HORUS_POLL_SECONDS`, default 15); optional retention
- Graphs: plays over time (hour / day / week / month, up to 90 days for hours), direct play vs transcode over time, bandwidth over time (with combined Total), plays by user / platform / hour of day / weekday, media type
- Earlier/later arrows, presets and All time, "View as table" on every chart, validated colour palette, light and dark themes

**Accounts and API**
- First-run account creation (the first person picks their username and becomes admin); admin and user roles; admins manage people; allow-list permissions for users; scrypt, sessions, rate-limited sign-in, setup code for remote first run
- API keys (hashed, shown once) and a read-only `/api/v1` data API for Grafana: plays, bandwidth, history, activity, servers; JSON and CSV

**Operations**
- Settings: time zone, poll interval, retention, HTTP port, "allow other computers", Restart, Shut down (disabled in containers), update address and check, backups
- Backups: manual and scheduled zips with retention, token-free export, restore from the UI (validates first, saves the current state first)
- Application logging: rotating file and console, secret scrubbing, level setting, Settings > Logs viewer
- Settings, Servers, Users, API and Logs live under Settings; Account page for your own password

**Project**
- 191 tests (fake responses, no live servers), `ruff` clean, private GitHub repo, README

**Added after v1**
- Delete backups; restore from an uploaded file (backup zip or token-free download), including from the first-run screen
- `Dockerfile`, `compose.example.yaml` and a Proxmox LXC install script (the Proxmox script has been run on a real host; the Docker image has not been built here)
- Import of past history (Plex history, Emby Playback Reporting plugin), safe to repeat
- Hide other people's activity from normal users (per-account Plex/Emby name link); per-user API keys
- Prometheus `/metrics`; notification channels (Discord, ntfy, webhook); read-only Seerr requests tab
- Auto / light / dark theme toggle; Windows "start at login" (`python -m horus_media startup install`)
- Tree-shaken ECharts (bundle about 1 MB to 650 kB); httpx deprecation warning silenced in tests
- Proxmox installer wizard (`scripts/proxmox/horus-lxc.sh`), run on a real Proxmox host: detects storage and bridges, validates input, shows numbered steps instead of raw output, picks the template for the CPU type, and creates the admin account during the install (blank password generates one), so the first-run setup code is never needed
- `python -m horus_media create-admin --username NAME` (password on standard input), used by the installers
- `.gitattributes` keeps LF line endings for shell scripts and Docker files
- Installer in the community-scripts style (`ct/horus.sh`, `install/horus-install.sh`, `json/horus.json`) and a release workflow (`.github/workflows/release.yml`) that builds the web UI and wheel on a `v*` tag; written but not run yet, because it needs a published release

## Next (suggested order)

**Before going public (Milestone 4)**
- [x] Licence: MIT (`LICENSE`, copyright "The Horus authors"). Before publishing, decide which account owns the repo: the current GitHub handle appears in the repo URLs in `ct/`, `install/` and `json/`, and as the commit author in the history
- [ ] Try the Dockerfile and the Proxmox script's update option; publish an image
- [ ] Push a `v*` tag so the release workflow publishes the download, then test `ct/horus.sh` on a real host (needs the repo public, or a GitHub token on the host); fill in the `logo` URL in `json/horus.json`
- [ ] Community-scripts submission is a long way off: a new app must be 6+ months old, actively maintained and have 600+ stars with release tarballs. The `ct/`, `install/` and `json/` files are kept in their format so it stays possible
- [ ] CI: tests and lint on every push
- [ ] Screenshots and release notes; version bump; a `horus-media` console command
- [ ] Pre-fill the update address with the real GitHub "latest release" link once the repo is public
- [x] Re-scan tracked files for personal details (homelab addresses, usernames, paths) before the visibility change (done 2026-10-28; repeat right before switching the repo to public)

**Features**
- [ ] Seerr: create requests / approvals (Horus is read-only for now); more notification kinds

**Tidy-ups**
- [ ] Code-split the Graphs page so the first load is smaller
- [ ] Switch tests to `httpx2` once FastAPI/Starlette require it (the warning is filtered for now)

## Known limits
- Emby history import uses the Playback Reporting plugin's `UserPlaylist` endpoint; field names were read from the plugin's source and have not been run against a live Emby, so try it on one server first
- Imported plays carry no bandwidth, and Plex imports have no play method (shown as Unknown)
- "Play started" notifications also fire for streams already running when Horus starts
- Plays are only recorded while Horus is running; very short plays are counted too
- Plex servers shared with you usually do not expose live sessions to non-owners, so activity for them will probably not work
- Emby direct-play bandwidth is an estimate from the file's bitrate (Emby reports no live rate)
