# Configuration

## Environment variables
- `HORUS_DB_PATH` (default `./data/horus.db`): SQLite file. An encryption key (`horus.key`) is created next to it on first run; server tokens are stored encrypted with it. Back up both files together and keep the key private.
- `HORUS_POLL_SECONDS` (default `15`; the Settings page overrides it once saved): how often Horus checks your servers and records plays. `0` turns recording off. Plays are only recorded while Horus is running. Set `TZ` (for example in Docker) so days and hours match your local time.
- `HORUS_PORT` / `HORUS_HOST` (default `8000` / `127.0.0.1`): where Horus listens. When set, they override the Settings page, which then shows them as locked.
- `HORUS_ADMIN_USER` with `HORUS_ADMIN_PASSWORD_FILE` (or `HORUS_ADMIN_PASSWORD`): create the first admin at start when none exists (see [Accounts and security](accounts-and-security.md#creating-the-admin-from-the-environment-docker)).
- `HORUS_OPEN_FIRST_RUN` (`1`): let Horus listen on the network, if "other computers may connect" is on, even before the first admin exists. The Proxmox installers set it in the service so you can create the first account in the browser. Without it Horus stays on this computer until an admin exists.
- `HORUS_AUTH_DISABLED` (`1` turns the login off; only behind something that handles sign-in for you, such as a reverse proxy with SSO).
- `HORUS_UPDATE_URL`: release address for "Check for updates" (otherwise set in Settings).
- `HORUS_CONTAINER` (`1`): tells Horus it runs in a container, so Restart and Shut down are disabled (also detected automatically in Docker and Kubernetes).

## Settings page
Time zone, poll interval, history retention, automatic backups, the daily update check, the HTTP port and network access are set in **Settings > General** and saved in the database. That page has four tabs: **General** (time zone, polling, retention, privacy), **Alerts and reports**, **Network**, and **Maintenance** (backups, update check, version, restart and shut down). They share one **Save settings** button, so a change on one tab is kept while you look at another. Servers, users, API keys and logs have their own tabs under Settings.

### Port, network access, restart and shut down
In **Settings > General > Network** you can change the HTTP port and choose whether other computers may connect. By default Horus listens on this computer only (`127.0.0.1`, port 8000). Changes apply after a restart, and the page has a **Restart now** button for that.

- It works however you started Horus. A restart always relaunches Horus as `python -m horus_media`, which uses the port saved in Settings, so if you started it with `uvicorn ... --port` the first restart after changing the port switches it over. Starting it with `python -m horus_media` in the first place is simplest.
- `HORUS_PORT` and `HORUS_HOST` override the settings (the page then shows them as locked).
- Opening Horus to other computers is only possible once an admin account exists.
- **Settings > General > Maintenance** also has **Restart** and **Shut down** (admins only). Shut down stops Horus until you start it again yourself.
- In Docker or Kubernetes the container owns the port, restart and stop, so those controls are switched off: change the port mapping and restart the container instead.

### Backups
Backups are zips of the database plus `horus.key`, written to a `backups` folder next to the database, either by hand or automatically (every whole number of days at a time you choose, 03:00 by default, with retention).

- They contain your encrypted server tokens and the key to decrypt them, so keep them private. They are never served over HTTP.
- The page offers a separate **Download** with the tokens removed.
- To restore, use the **Restore** button next to a backup, or **Restore from a file...**. Horus checks the backup, saves the current state first (so a restore can be undone), then swaps it in. Restoring also restores the accounts and API keys that were in it.
- By hand: stop Horus, unzip over `horus.db` and `horus.key` in the data folder, start it again.

### Update checks
Update checks only contact the *Release address* in Settings > General > Maintenance (or `HORUS_UPDATE_URL`), a GitHub-style "latest release" URL. On a new install the address of the Horus project is filled in and Horus looks for a newer release once a day (at 04:00 by default; change the time in Settings). It only reads the latest-release page, sends nothing about you or your servers, and never installs anything. Untick *Check for updates once a day*, or clear the address, to turn it off.

When a newer version exists, Settings shows the update steps for how your copy runs (Docker, a Proxmox container, or a manual install) with a Copy button. On a Proxmox container that can write to its own environment, an **Update now** button appears instead (see [Installing](install.md#updating)). Docker and manual installs are never updated by Horus itself.

### Logs
Horus writes an application log to `logs/horus.log` next to the database (rotating, about 10 MB at most) and to the console. Read it in **Settings > Logs** (filter by level, search, download) or open the file. It records starts and stops, servers added or removed, connection tests, plays starting and ending, servers going unreachable and coming back, sign-ins, user and settings changes, backups and restores. How much is recorded is set on the same page under *Recording detail* (default *Normal*; use *Everything* when troubleshooting).

Tokens, API keys, passwords and credentials inside URLs are scrubbed before anything is written, and the code avoids logging them in the first place. Plays include usernames and titles, so treat a shared log like any other personal data.

### Theme
The header button cycles Auto (follow the system), Light and Dark. The choice is remembered in that browser.
