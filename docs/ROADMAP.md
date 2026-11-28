# Roadmap

Status as of 2026-11-28: version 0.4.22. The original v1 scope is complete and the project is public (repository and Docker image). Per-version details are in [CHANGELOG.md](../CHANGELOG.md). `1.0.0` comes once `ct/horus.sh` has run on a real Proxmox host and the published image has had real use.

## Done

**Connections**
- Plex, Emby and Jellyfin clients behind one `MediaServer` interface (`test_connection`, `get_sessions`, posters; reads only, apart from the admin-only `stop_session`)
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
- Plays and hours watched per month for the last 12 months, stacked by server; the Graphs page loads on demand
- Earlier/later arrows, presets and All time, "View as table" on every chart, validated colour palette, light and dark themes
- Import of past history (Plex history, the Playback Reporting plugins of Emby and Jellyfin), safe to repeat

**Accounts and API**
- First-run account creation (the first person picks their username and becomes admin); admin and user roles; admins manage people; scrypt, sessions, sign-in rate limited per username and address (`HORUS_TRUSTED_PROXIES` for the real address behind a proxy); an email address works as a username
- API keys (hashed, shown once), per-user keys, and a read-only `/api/v1` data API for Grafana: plays, bandwidth, history, activity, servers; JSON and CSV
- Control keys (off by default, always expiring, never able to touch users, keys, sign-in, Stop stream, backup downloads, uploaded restores or the release address, every use logged) and `scripts/horusctl.py`
- Hide other people's activity from normal users (per-account Plex/Emby name link)
- Prometheus `/metrics`, including playback problem counts by kind
- Playback problems: buffering (Plex), stalled playback, slow transcodes and Emby's failed direct play are noticed while recording, marked on the Activity page, graphed and listed on the Graphs page, available at `/api/v1/problems` and as an opt-in notification
- Browser protections: changes accepted only from Horus's own pages, a content security policy and related headers on every response, no framing, picture-only posters, no generated API pages

**Operations**
- Settings: time zone, poll interval, retention, HTTP port, "allow other computers", Restart, Shut down (disabled in containers), update address and daily check with a time of day, backups
- Backups: manual and scheduled zips (with a time of day) and retention, token-free export, restore from the UI or an uploaded file (validates first, saves the current state first), delete backups
- Settings menu in seven entries (a drop-down list on a phone); Account, Theme and Sign out in a menu under your name; every page laid out to fit a phone, with the desktop look unchanged
- Application logging: rotating file and console, secret scrubbing, level setting, Settings > Logs viewer
- Update check with install-specific update steps; an admin-only Update now button for a Proxmox container that can write to its own environment (works on a real container, confirmed 2026-10-29) (Docker and manual installs are never updated by Horus)
- Notification channels (Discord, ntfy, Telegram, Gotify, Pushover, webhook; each can be edited in place), optional alerts, weekly/monthly summary reports with CSV. (A read-only Seerr requests tab was added and then removed on 2026-10-29: it had nothing to do with plays or stats)
- Auto / light / dark theme toggle; Windows "start at login"; `create-admin` and `configure` commands; a `horus-media` command next to `python -m horus_media` (not `horus`: another package already installs that name)

**Installing and project**
- `Dockerfile`, `compose.example.yaml` and the image on ghcr.io (amd64 and arm64, each built and started on a machine of its own type), published by a tag workflow
- Proxmox installer wizard (`scripts/proxmox/horus-lxc.sh`), run on a real host; the first admin is created in the browser (or optionally during the install with `ADMIN_USER`/`ADMIN_PASSWORD`)
- Community-scripts style installer (`ct/horus.sh`, `install/horus-install.sh`, `json/horus.json`) and a release workflow that builds the web UI and wheel on a `v*` tag (its install steps ran in a throwaway container; `ct/horus.sh` itself ran on a real Proxmox host on 2026-10-29: 0.3.0 installed, first admin created in the browser, Plex added, history imported)
- MIT licence, `CONTRIBUTING.md`, `SECURITY.md`, issue and pull request templates, Dependabot, CODEOWNERS
- Public repository with secret scanning, private vulnerability reporting, CodeQL, Dependabot alerts, protected `main`, SHA-pinned Actions
- CI (tests, lint, Docker image, web build) on every push and pull request
- Build supply chain: hash-locked Python dependencies and digest-pinned base images for the Docker image, signed build provenance for the release file and the image, release notes taken from the changelog, immutable releases and protected version tags
- 487 tests (fake responses, no live servers), `ruff` clean; README and docs

**Verified**
- Backup Download then Restore across installs (Proxmox container to Docker); Emby history import; lint and the full suite on real Linux; the Docker image in CI and a real Docker host, including an anonymous pull; all tests on emulated aarch64
- With the published image on a real Docker host (2026-11-20, 0.4.18): `HORUS_ADMIN_USER` with a password file creates the admin before the first page can be opened, the first-run screen is closed, the password shows in neither `docker inspect` nor the log, and a restart changes nothing; a backup made by 0.4.4 restores on the first-run screen of 0.4.18, the database is brought up to date and the old account signs in

## Next

- [ ] Try Stop stream on a real Emby and a real Jellyfin (never without the author's say-so)
- [ ] Try the Jellyfin history import against a real Jellyfin with the Playback Reporting plugin installed
- [ ] Real-server test run by hand (a real Plex and Emby in a throwaway setup) to catch API changes the fakes can't
- [ ] Try the arm64 image on a real small board (it is built and started on an arm64 machine at GitHub, nothing more)
- [ ] Mobile: the responsive web layout is done (every page fits a phone-sized window; not yet tried on a real phone). Next if wanted: an installable PWA, a native app only if that falls short
- [ ] Try Telegram, Gotify and Pushover channels against the real services (built from their API descriptions, tested with stand-ins); more kinds on request (email was left out for now)
- [ ] Playback problems: check the Plex transcode speed fields against a real transcoding stream
- [ ] GitHub Discussions
- [ ] Community-scripts submission is a long way off: a new app must be 6+ months old, actively maintained and have 600+ stars with release tarballs. The `ct/`, `install/` and `json/` files are kept in their format so it stays possible

(A watchlist view was dropped.)

**Tidy-ups**
- [ ] Switch tests to `httpx2` once FastAPI/Starlette require it (the warning is filtered for now)

## Known limits
- Jellyfin (0.4.20) was built from the API description a Jellyfin server publishes about itself and tested with stand-ins. Its history import reads the Playback Reporting plugin (`user_list`, `type_filter_list`, `PlayActivity`, `GetItems`; names read from the plugin's source) with read calls only. It was read for real on 2026-11-28 (Jellyfin 12.4, Playback Reporting 19): the plugin counts a play on the server's own day but lists it under the UTC day, so since 0.4.22 Horus reads the neighbouring days too and turns the server's clock time into a real time with the Horus time zone (wrong by the difference when Jellyfin and Horus are in different time zones). Only a single film has been read this way; an episode and a long history have not. A real direct play and a real transcode were read correctly (2026-11-28). Its transcode speed is worked out between polls from the completion figure, and a conversion at least a minute ahead of the player is taken to be resting; a real slow or resting Jellyfin transcode has not been seen
- Emby history import uses the Playback Reporting plugin's `UserPlaylist` endpoint; field names were read from the plugin's source and imports have only been tried on one Emby server
- Imported plays carry no bandwidth, and Plex imports have no play method (shown as Unknown)
- A stream that begins in the few seconds before Horus first reads a server counts as already playing, so it gets no "play started" notification
- Plays are only recorded while Horus is running; very short plays are counted too
- Plex servers shared with you usually do not expose live sessions to non-owners, so activity for them will probably not work
- Playback problems are a best guess from what the servers report once per poll: anything shorter than the poll interval is missed, Emby reports no buffering state (it shows as Stalled), and a stream with no position (live TV) cannot be seen to stall
- Emby direct-play bandwidth is an estimate from the file's bitrate (Emby reports no live rate)
- Bandwidth older than 30 days is kept as five-minute averages with their peak. For that older data the combined Total peak adds the servers' own peaks, which can be a little higher than the true peak of both together
- Until the first account exists, anyone who can open the page can create it, unless `HORUS_ADMIN_USER` is set (Docker listens on the network from the start)
