# Roadmap

Status as of 2026-10-28. The app is feature-complete for the original v1 scope and is being prepared for a public release.

## Done

**Connections**
- Plex and Emby clients behind one `MediaServer` interface (`test_connection`, `get_sessions`, posters; reads only, apart from the admin-only `stop_session`)
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
- First-run account creation (the first person picks their username and becomes admin); admin and user roles; admins manage people; allow-list permissions for users; scrypt, sessions, rate-limited sign-in; an email address works as a username. (A setup code for remote first run existed until 2026-10-28 and was removed at the author's request: until the first account exists, anyone who can open the page can create it, so installers that listen on the network should create the admin up front.)
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
- Proxmox installer wizard (`scripts/proxmox/horus-lxc.sh`), run on a real Proxmox host: detects storage and bridges, validates input, shows numbered steps instead of raw output, picks the template for the CPU type, and creates the admin account during the install (blank password generates one), so nobody else can claim the first account
- `python -m horus_media create-admin --username NAME` (password on standard input), used by the installers
- `.gitattributes` keeps LF line endings for shell scripts and Docker files
- Installer in the community-scripts style (`ct/horus.sh`, `install/horus-install.sh`, `json/horus.json`) and a release workflow (`.github/workflows/release.yml`) that builds the web UI and wheel on a `v*` tag; written but not run yet, because it needs a published release

## Next (suggested order)

**Before going public (Milestone 4)**
- [x] Licence: MIT (`LICENSE`, copyright "The Horus authors"). Before publishing, decide which account owns the repo: the current GitHub handle appears in the repo URLs in `ct/`, `install/` and `json/`, and as the commit author in the history
- [x] Dockerfile built and smoke-tested on a real Docker host and in CI; the Proxmox script's update option works. A workflow (`docker-publish.yml`) publishes the image to ghcr.io on every version tag (amd64 only; arm64 is untested). It has been run once from `main` (2026-10-28): `ghcr.io/watchful-horus/horus-media:edge` exists and is private. The workflow uses only the `docker` command because the repo's Actions policy allows GitHub's own actions only. Still to do: make the package public when the repo goes public
- [x] `v0.1.0` tagged and released (private; first public preview, `1.0.0` once the Proxmox community install and a published Docker image are verified). The install steps of `ct/horus.sh` were run for real in a throwaway container (2026-10-28): extract, `uv` environment, wheel install, admin account, port saved, Horus up with the port and network editable. Still to do: run `ct/horus.sh` itself on a Proxmox host (needs the repo public, or a GitHub token on the host); fill in the `logo` URL in `json/horus.json`
- [x] `CONTRIBUTING.md`, `SECURITY.md`, issue and pull request templates, Dependabot and CODEOWNERS are in place
- [ ] **On the day the repo goes public** (these settings are only available, or free, once it is public): repeat the personal-details scan; Settings > Code security: turn on private vulnerability reporting, Dependabot alerts and secret scanning; Settings > Branches (or Rules): protect `main` and require the CI checks to pass; Settings > General: add a description and topics, and decide on Issues and Discussions; make the ghcr.io package public (profile > Packages > horus-media > Package settings); then change the visibility
- [ ] Community-scripts submission is a long way off: a new app must be 6+ months old, actively maintained and have 600+ stars with release tarballs. The `ct/`, `install/` and `json/` files are kept in their format so it stays possible
- [x] CI: tests, lint, Docker image and web build on every push and pull request
- [x] Screenshots (README). Still to do: release notes; version bump; a `horus-media` console command
- [ ] Pre-fill the update address with the real GitHub "latest release" link once the repo is public
- [x] Re-scan tracked files for personal details (homelab addresses, usernames, paths) before the visibility change (done 2026-10-28; repeat right before switching the repo to public)

**Features**
- [x] Stop stream (2026-10-29): an admin can end a playing stream from the Activity page, with a confirmation and an optional message for the viewer (Plex terminate, needs Plex Pass; Emby Playing/Stop). Signed-in admins only: never an API key or control key. Logged by name. Paused streams show an amber Paused badge. Tested with fake servers and a scratch copy only, not against a real Plex or Emby.
- [x] Rename a server (everything follows, since history joins on the id); optional alerts (transcodes, bandwidth, new person); weekly/monthly summary report with an on-screen view and CSV (2026-10-28). Watchlist view was dropped
- [ ] Seerr: create requests / approvals (Horus is read-only for now); more notification kinds
- [x] Control keys (2026-10-28): API keys that can run Horus as their admin owner for testing and automation. Off by default (Settings > API), always expiring, never able to touch users, keys or sign-in, every use logged. `scripts/horusctl.py` calls the API with such a key without printing it
- [ ] Docker: create the first admin from environment variables (for example `HORUS_ADMIN_USER` and a password file), so a container never shows an open first-run screen to the network (the setup code was removed on 2026-10-28)

**After the next changes land**
- [x] `0.2.0` cut on 2026-10-29: Stop stream (admin only; tested with fake servers, not yet on a real Plex or Emby), an amber Paused badge, and activity totals per server and overall with a local/remote split. Horus is no longer strictly read-only because of Stop stream.
- [x] `0.1.4` cut on 2026-10-29: Settings shows how to update for the way Horus runs (Docker, Proxmox container or manual install) with Copy buttons; Horus still never updates itself.
- [x] `0.1.3` cut on 2026-10-29: server rename, optional alerts, weekly/monthly summary reports, Network settings fix for containers, update check on by default with a time of day, a time of day for automatic backups, backup path hardening (CodeQL), redrawn graphs (solid lines, stacked weekday bars, calmer bandwidth), arm64 marked supported in the Proxmox script, SHA-pinned Actions. The repo went public on 2026-10-29.
- [x] `0.1.2` cut on 2026-10-28 (adds control keys; `0.1.1` added email usernames, no setup code and newer build tools): release and ghcr.io image published by the tag workflows. `v0.1.0` and `v0.1.1` are the older releases. Still to do: `1.0.0` once the Proxmox community install and the published image have had real use
- [x] Verified on 2026-10-28: Download a backup on one install and Restore it on another (a Proxmox container to a Docker container): servers, tokens, history and bandwidth all came across; the Emby history import works; lint and the full suite pass on real Linux in a container (CI covers it on every push); `ct/horus.sh`'s own install steps ran in a throwaway container
- [ ] Not yet tried on real hardware: `ct/horus.sh` itself on a Proxmox host (needs the repo public, or a GitHub token on the host); the Docker image on arm64 (only amd64 is built; the app and its 221 tests do pass on aarch64 Python 3.12 under QEMU emulation, 2026-10-28, which is why `var_arm64` is `yes`); restoring a backup that is older than the current database version

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
