<p align="center"><img src="docs/logo.svg" width="96" height="96" alt="Horus logo"></p>

# Horus

A self-hosted dashboard for Plex and Emby. See what is playing on both servers right now, and how your libraries get watched over time.

> Status: early release (0.2.0), public and tested with Docker and a Proxmox container. Works with Plex and Emby (not affiliated with either).

## Features
- Live activity from Plex and Emby in one view, with posters and full stream details
- Play history recorded locally (SQLite) from the moment you add a server, with optional import of past history
- Graphs: plays over time by hour, day, week or month; direct play vs transcode; bandwidth; plays by user, platform, hour and weekday; media types
- Accounts with admin and user roles (an email address works as a username), API keys, and a read-only data API for Grafana and Prometheus
- Notifications (Discord, ntfy, webhook), optional alerts, weekly or monthly summary reports, and a Seerr requests tab
- Backups and restore, application logs, and restart / shut down from the web UI
- Light and dark themes
- Reads from your servers and changes nothing, with one exception: an admin can press **Stop stream** on the Activity page to end a stream (a signed-in admin only, never an API key or control key; Plex needs Plex Pass for this). Paused streams show an amber **Paused** badge, and a summary at the top adds up the streams and bandwidth per server and in total, split into local and remote (a paused stream counts as a stream but adds no bandwidth)

## Screenshots
Graphs in the dark theme, and in the light theme, then bandwidth and the hour and weekday breakdowns. The data shown is invented.

<p align="center"><img src="docs/screenshots/graphs-dark.png" width="48%" alt="Horus graphs, dark theme"> <img src="docs/screenshots/graphs-light.png" width="48%" alt="Horus graphs, light theme"></p>
<p align="center"><img src="docs/screenshots/graphs-bandwidth-dark.png" width="48%" alt="Bandwidth over time"> <img src="docs/screenshots/graphs-breakdowns-dark.png" width="48%" alt="Plays by hour of day and by weekday, stacked by media type"></p>

## Quick start
**Docker** (needs Docker with Compose): copy `compose.example.yaml` to `compose.yaml`, set your time zone, and run `docker compose up -d`. The image is `ghcr.io/watchful-horus/horus-media`.

Then open port 8000 and **create the first account straight away**. The container listens on the network from the start, so until the account exists anyone who can reach the page could create it.

Other ways to install (a Proxmox VE container with a guided script, or from source on Windows, Linux or macOS) are in [docs/install.md](docs/install.md).

Next, add a server under **Settings > Servers**:
- **Plex:** *Sign in with Plex* (or paste an `X-Plex-Token`).
- **Emby:** create an API key in the Emby dashboard (Advanced > API Keys) and paste it in.

Horus starts recording plays as soon as a server is added. See [Servers, history and integrations](docs/servers-and-integrations.md) to import older history.

## Documentation
| | |
|---|---|
| [Installing](docs/install.md) | Docker, Proxmox container, from source, start at login, moving to a new install |
| [Configuration](docs/configuration.md) | Environment variables, Settings, port and network, backups, update checks, logs |
| [Accounts and security](docs/accounts-and-security.md) | First account, roles, privacy between users, API keys |
| [Servers, history and integrations](docs/servers-and-integrations.md) | Adding servers, importing history, notifications, alerts, reports, Seerr |
| [API](docs/api.md) | Data API for Grafana, Prometheus metrics, control keys |
| [Roadmap](docs/ROADMAP.md) and [Changelog](CHANGELOG.md) | What is done and what is next |

## Contributing and security
Help is welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) for the ground rules and how to set up. To report a security problem privately, see [SECURITY.md](SECURITY.md).

## Licence
Horus is released under the [MIT licence](LICENSE). Plex and Emby are trademarks of their owners; Horus is not affiliated with or endorsed by either.
