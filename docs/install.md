# Installing Horus

Pick one. All three keep your data (database, encryption key, backups, logs) in one folder, so moving later is easy (see [Moving to a new install](#moving-to-a-new-install)).

## Docker
Needs Docker with Compose. Copy `compose.example.yaml` to `compose.yaml`, set your time zone, and run `docker compose up -d`. The image is `ghcr.io/watchful-horus/horus-media` (amd64).

Open port 8000 and **create the first account straight away**: the container listens on the network from the start, so until the account exists anyone who can reach the page could create it. To avoid that window, set `HORUS_ADMIN_USER` and `HORUS_ADMIN_PASSWORD_FILE` (lines to uncomment are in `compose.example.yaml`); the account is then created when the container starts. See [Accounts and security](accounts-and-security.md#creating-the-admin-from-the-environment-docker). Data lives in the `horus-data` volume. Restart and shut down are done with Docker, not the web UI, and so is changing the port (change the port mapping and restart the container).

## Proxmox VE container
`scripts/proxmox/horus-lxc.sh` creates a small Debian 13 container, installs Horus as a service and prints the address. Run it on the Proxmox host as root.

- It is a wizard (default or advanced settings, with menus for storage and network). It does not create a login: when it finishes it prints the address, and you open it and **create the administrator account yourself** (your own username and password). Do that straight away, because until the account exists anyone on your network could. For automation, `ADMIN_USER` and `ADMIN_PASSWORD` create the account during the install instead.
- The install shows one line per step with a spinner and keeps the full output in a log file (`HORUS_VERBOSE=1` prints everything instead).
- Point `HORUS_PACKAGE` at a Horus wheel and copy it to the host first, for example `HORUS_PACKAGE=/root/horus_media-0.4.2-py3-none-any.whl bash horus-lxc.sh`. Build a wheel with `cd web && npm run build && cd .. && pip wheel . --no-deps -w dist`.
- CPU, RAM, disk, storage, network and port can be set with environment variables (listed at the top of the script).
- Update later with `bash horus-lxc.sh update <container id>`; the data is kept.
- Restart and shut down work from the web UI.

`ct/horus.sh`, `install/horus-install.sh` and `json/horus.json` are the same installer in the community-scripts style, for a possible future submission. It also leaves the first account to the browser. (Both installers set `HORUS_OPEN_FIRST_RUN=1` in the service, which lets Horus listen on the network before the first admin exists; this needs Horus 0.3.0 or newer.) `ct/horus.sh` has not been run on a real Proxmox host yet, so use the wizard above.

## From source (Windows, Linux, macOS)
Needs Python 3.12+ and, to build the web UI, Node 20+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (use source .venv/bin/activate on Linux/macOS)
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
python -m horus_media         # 127.0.0.1:8000 unless changed in Settings or HORUS_HOST / HORUS_PORT
```
Open http://127.0.0.1:8000/. The first visit asks you to create the first account. For development, `npm run dev` in `web/` serves the UI on :5173 with hot reload and proxies `/api` to a running Horus, and `uvicorn horus_media.main:app --reload` gives the backend hot reload (the port is then set on the command line). See [CONTRIBUTING.md](../CONTRIBUTING.md) for the checks to run.

### Start at login (Windows)
```
python -m horus_media startup install
python -m horus_media startup status
python -m horus_media startup remove
```
This adds or removes one entry under your own account's Run key (no administrator rights) that starts Horus without a console window, using the data folder you installed it from.

## Command line helpers
- `python -m horus_media create-admin --username NAME` creates the first admin (the password is read from standard input). Scripts and automation can use it.
- `python -m horus_media configure --port N [--listen-all]` saves the port and whether other computers may connect, without the web UI. The installers use it. `--db PATH` (before the command) picks another database file.

## Moving to a new install
On a fresh install the first screen has **Already use Horus? Restore from a backup instead**. Choose a backup zip (Settings > Backups > Download on the old install) and your accounts, servers and history come back. In an existing install use **Restore from a file...** under Settings > Backups. Backups can be deleted from the same list.

## Updating
Horus checks once a day for a newer release and, when there is one, shows how to update in Settings.

- **Docker and manual installs:** the steps for how your copy runs, with a Copy button. Horus never updates these itself.
- **Proxmox container:** an **Update now** button (admins only, from a signed-in session; no API or control key can press it). It makes a backup, downloads the release from GitHub, installs it into Horus's own environment and restarts. It appears only when the container can write to that environment, which is true for containers made or updated with the current `horus-lxc.sh` and for the community-style installer (which runs as root). On an older container, run `bash horus-lxc.sh update <container id>` once and the button appears from then on. See [Configuration](configuration.md#update-checks).
