# Changelog

Newest first. Each version is a tag on GitHub, with a Docker image and a release download.

## 0.4.22 (2026-11-28)

**Fixed**
- **Jellyfin history import missed plays.** On a real Jellyfin with the Playback Reporting plugin, the plugin counts a play on the day the server's own clock showed but lists it under the UTC day. Horus asked for the list of the counted day only, so a play made late in the evening (on a server behind UTC) or early in the morning (ahead of UTC) was never found; on a server several hours from UTC an import could find nothing at all. Horus now also reads the day before and the day after, and takes each play once.
- **Imported Jellyfin plays now get their real time.** The plugin gives the server's clock time without a time zone. Horus used to read it as UTC, which put plays hours off and let a play Horus had already recorded live be imported a second time. It is now read in Horus's time zone (**Settings**, or the machine's own when none is set), so it is right whenever Horus and Jellyfin are in the same time zone, and plays recorded live are skipped as on the other servers. [Details](docs/servers-and-integrations.md#importing-past-history).
- Seen working against a real Jellyfin 12.4 with Playback Reporting 19: the play was found, its time came out right and it matched the play recorded live.

## 0.4.21 (2026-11-28)

**New**
- **Transcode speed for Jellyfin.** Jellyfin says how much of a file it has converted, so Horus now works the speed out from how far that moves between two polls, as it does for Emby. The Activity page shows it from the second poll of a stream, and **Slow transcode** problems (and their notification) now work on Jellyfin too.
- Jellyfin never says when a conversion is resting because it is far enough ahead. Horus takes a conversion that is a minute or more ahead of the player and not moving at playback speed to be resting: the Activity page then shows **Ahead of playback** and no problem is raised.
- The numbers behind this were checked against a real Jellyfin transcode (its own frame rate and its completion figure agreed, about 27 times playback speed). A real slow or resting Jellyfin transcode has not been seen yet.

**Changed**
- A real Jellyfin stream has now been read: direct play, a switch to a transcode, bitrate, position and the reason for transcoding all showed correctly.

## 0.4.20 (2026-11-28)

**New**
- **Jellyfin.** Add a Jellyfin server under **Settings > Servers > Add a server manually** with its address and an API key (Jellyfin: **Dashboard > API Keys**). It then works like the others: live activity with posters and stream details, recorded history, graphs, notifications, the per-server summary and, for an admin, **Stop stream**. **Test connection** also checks the key.
- **Import history** works for Jellyfin through its Playback Reporting plugin (**Dashboard > Plugins > Catalog**), with read calls only. Times come to the minute and are read as UTC. [Details](docs/servers-and-integrations.md#importing-past-history).
- Playback problems on Jellyfin: stalled playback, failed direct play and repeated interruptions, plus the reason for a transcode on the Activity page. Slow transcodes are not detected there.
- Built from the API description a Jellyfin server publishes and tested against stand-ins. On a real Jellyfin, connecting with a key and reading an idle server were seen to work. A real playing stream has not been read yet, the history import has not run against a real plugin, and Stop stream has not been pressed on a real Jellyfin: please report what you see.
- This version changes the database (the servers table is rebuilt; servers, history and everything else are kept). A backup made by 0.4.20 cannot be restored on an older version.

## 0.4.19 (2026-11-20)

**New**
- **Three more kinds of notification channel: Telegram, Gotify and Pushover.** Each asks for its own two values in labelled boxes (a bot token and chat ID, a server address and app token, an app token and user key). They are stored encrypted and never shown again, like channel addresses. Everything else works as for the other channels: events, Edit, Send test, pause. Telegram messages are plain text.
- These three were built from the services' published API descriptions and tested against stand-ins only. Press **Send test** after adding one, and please report what happens.
- This version changes the database (the notification channels table is rebuilt; your channels are kept). A backup made by 0.4.19 cannot be restored on an older version.

**Changed**
- **Horus reads better on a phone.** Phones only (720 px and narrower); nothing changes in a desktop browser.
  - No page scrolls sideways any more. On Graphs the range buttons are on one row, the two dates on the next, and the arrows and Apply below; the latest playback problems are one small block each instead of a wide table; long names beside the bar charts are shortened (the full name shows on tap and in *View as table*).
  - Buttons, drop-downs and text boxes are tall enough for a finger, and text boxes use larger text so an iPhone does not zoom in when one is tapped.
  - Logs show the time and level on one line with the message full width below. API keys, notification channels, backups and users lay their details and buttons out to fit the width.
  - Checked at a phone-sized window in a desktop browser, not yet on a real phone.

**Fixed**
- **The first admin made from `HORUS_ADMIN_USER` is now recorded in the log.** That step ran before logging was switched on, so the line saying the account was created never appeared, and a problem with it (a password file that cannot be read, a password that is too weak) reached the console but not the log file or Settings > Logs. Found while trying the variables with the published image on a real Docker host; the variables themselves worked.

## 0.4.18 (2026-11-20)

**New**
- **A `horus-media` command.** Installing the package now also installs a `horus-media` command that does what `python -m horus_media` does: `horus-media` starts Horus, and `horus-media startup ...`, `horus-media create-admin ...` and `horus-media configure ...` work the same way. The old form keeps working, and the installers, the Docker image and the Restart button still use it, so nothing changes for an existing install.

## 0.4.17 (2026-11-20)

**New**
- **The Docker image is now also published for arm64** (64-bit ARM boards and servers), next to amd64. The tags are the same as before (`0.4.17`, `0.4`, `latest`) and Docker picks the right one for your machine, so `compose.yaml` does not change. Each one is built and started on a machine of its own type before anything is published. Not yet tried by us on a real small board such as a Raspberry Pi; please report how it goes.
- Each version also has a tag per processor type (`0.4.17-amd64`, `0.4.17-arm64`), which the usual tags are made from. You do not need them.

## 0.4.16 (2026-11-20)

**New**
- **Playback problems in `/metrics`**, for Prometheus and Grafana. `horus_playback_problems_total` is how many problems Horus has in its history and `horus_playback_problems_active` is how many are going on right now. Both are split by server and by kind (`buffering`, `stalled`, `slow_transcode`, `direct_play_error`). As with the other metrics, no user names or titles appear. The total falls when old history is removed by the retention setting, as `horus_plays_recorded_total` does.

**Fixed**
- **No more "started playing" notifications for streams that were already running.** When Horus starts, or a server is added or first answers, the streams it finds there are recorded as before but no *Someone starts playing* notification is sent for them; the log says *already playing*. Anything that starts after that notifies as usual, and so does the end of every play.
- **After restoring a backup, Horus forgets what it remembered about streams from before the restore.** Until now it could mark a playback problem against the wrong record after a restore, until the stream ended.

## 0.4.15 (2026-11-19)

**New**
- **The version is shown in the top bar**, small, next to the Horus name (for example *v0.4.15*), once you are signed in. For an admin it is a link to Settings > Backups and updates, where *Check for updates* is. The sign-in page does not show it.

**Fixed**
- **On a phone a long user name no longer pushes the top bar to three rows.** The name shortens to fit beside the logo.

## 0.4.14 (2026-11-19)

**New**
- **Slow transcodes are now noticed on Emby too.** Emby does not report how fast it converts a stream (checked on a real server), so until now Horus could only notice a slow transcode on Plex. Emby does report how far into the file it has converted, so Horus now works the speed out itself: film time converted between two polls, divided by the time that passed. A server that converts in bursts and waits in between still averages at least 1x; one that cannot keep up stays below it and is recorded as a *Slow transcode*, as on Plex. Readings taken while Emby waits, while the player is paused, or across a seek are not used.
- **The Activity page shows it:** an Emby stream that is being converted shows *Transcode speed* from its second poll (about 15 seconds in) while Emby is working, and *Ahead of playback* while Emby waits. `/api/v1/activity` carries both, and for Plex nothing changes.

## 0.4.13 (2026-11-19)

**Changed**
- **The Settings menu is shorter and in a more useful order:** Servers, Users, Notifications, API, General, Backups and updates, Logs (seven entries instead of nine).
  - **Notifications** is one page: the channels, the alert limits and report schedule, and the report to see, download or send now. Before, these were split between *Alerts and reports* and *Integrations*, and each page sent you to the other.
  - **General** now also holds the port and network setting (was *Network*).
  - **Backups and updates** is the new name of *Maintenance / Updates*.
  - Settings opens on Servers. Old addresses and bookmarks such as `#settings/integrations`, `#settings/alerts` and `#settings/network` open the page that now holds them.
- **Your name in the top bar is now a small menu** with Account, Theme and Sign out, in place of three separate buttons.
- **On a phone** the top bar is two rows instead of three, and the Settings menu is a drop-down list instead of a row that scrolled sideways with most entries out of sight.

## 0.4.12 (2026-11-19)

**New**
- **Hours watched per month.** The Graphs page has a second 12-month chart under *Plays per month*: hours watched per month, stacked by server. Time is counted from the start of a play to the last time Horus saw it, so a pause counts, and one play counts for at most 12 hours. Plays imported from a server's own history have no length and add no hours; a note under the chart says how many of the plays those are.

**Fixed**
- **A healthy converted stream can no longer be recorded as a slow transcode.** Each time Plex starts converting the next piece of a stream it reports a speed of 0 for about a second, before it has measured anything. Horus read that as a server converting at 0x; seen on two polls in a row it would have recorded a *Slow transcode*. A speed of 0 is now treated as "not known yet": no speed is shown and no problem is raised. A stream that really stops is still caught as *Stalled*.

## 0.4.11 (2026-11-19)

**Fixed**
- **A healthy converted stream no longer shows "Transcode speed 0x".** A server that is far enough ahead of playback pauses converting, and Plex then reports a speed of 0. The Activity page showed that number, which reads as a server that cannot keep up. It now says *Ahead of playback*. No playback problem was ever recorded for this; only the display was misleading.
- **`/api/v1/activity`:** `transcode_speed` is empty for such a stream and the new `transcode_throttled` is true, so a dashboard can tell "paused ahead" from "too slow".

## 0.4.10 (2026-11-19)

**Security**
- **The change-password form now counts wrong tries.** After five wrong entries of the current password in a minute, that account has to wait a minute, the same as on the sign-in page. Before, a browser left signed in could be used to guess the password without any limit. The count is per account, so other people are not held up, and a correct entry clears it.
- **A read key can be given an end date.** When you create an API key under Account or Settings > API you can now choose *No end date* (as before, and still the default), 30, 90 or 365 days. A key past its date stops working and is shown as expired; keys you already have are not changed. In the API this is `expires_in_days` on `POST /api/keys` and `POST /api/my-keys`.

**Behind the scenes**
- The web UI is built with vite 8.3.8.
- Dependabot now proposes only refreshed builds of the Python and Node versions the Docker image already uses; moving to a newer Python or Node is decided separately.

## 0.4.9 (2026-11-19)

**New: playback problems**
- **Horus notices when a stream has trouble.** While it records history it now watches each playing stream for four things: the player says it is *buffering* (Plex), playback has *stalled* (not paused, yet the position has not moved for 40 seconds; Plex and Emby), the server *converts slower than the stream plays* (a slow transcode), and Emby had to convert because *direct play failed*. A problem has to be seen on two polls in a row, so a seek or the short buffer at the start of a play is not counted.
- **On the Activity page** a stream with a problem gets a red mark next to its title (Buffering, Stalled, Slow transcode, Direct play failed, or Keeps buffering after three interruptions in one play). A transcoded stream also shows how fast the server converts it and, on Emby, why it is converted (for example "Bitrate over the player's limit").
- **On the Graphs page** there are four new cards for the chosen dates: problems over time by kind, problems by user, problems by player, and a list of the latest ones with how long each lasted.
- **A new notification event**, *A stream has a playback problem*, can be ticked on a channel under Settings > Integrations. It is off until you tick it and is sent once per kind for each play, and once more if the play keeps buffering.
- **`GET /api/v1/problems`** returns the recorded problems as JSON or CSV, and `/api/v1/activity` gains `transcode_speed` and `transcode_reason`. The `state` of a stream can now also be `buffering`.

**New: edit a notification channel**
- **Settings > Integrations has an Edit button on each channel.** It opens the channel in place and lets you change its name, type, address and events; before, the only way was to remove the channel and add it again. The address is still never shown: the field starts empty, leaving it empty keeps the current address, and typing a new one replaces it. Changing the type asks for a new address. Saving sends nothing; use Send test to check a new address.
- **A channel whose address can no longer be read can be repaired** with Edit by entering the address again, keeping its name and events.

**Good to know**
- Horus only sees what Plex and Emby report when it asks, every poll interval (15 seconds by default). A hiccup shorter than that is missed, and "stalled" is Horus's own conclusion, not something the server says. Nothing is recorded while recording is off (poll interval 0).
- Emby has no buffering state, so buffering there shows up as Stalled.
- The transcode speed fields for Plex are read as Plex sends them but have not yet been checked against a real transcoding stream.
- Problems are removed together with old history (Settings > General, *Keep history for*). Only LAN or WAN is stored with a problem, never an address.

**Upgrading**
- One database change runs automatically at start (migration 13 adds a table for playback problems). A backup made with this version cannot be restored into 0.4.8 or older.

## 0.4.8 (2026-11-19)

**Data**
- **Old bandwidth samples are thinned.** Horus records one sample per server every 15 seconds, about two million a year, and kept them all. Samples older than 30 days are now replaced by five-minute averages that keep the peak, once a day. Graphs of older periods keep their shape; the database stops growing without limit and long ranges load faster. This cannot be undone, so the first time it runs Horus writes a full backup first (Settings > Maintenance / Updates lists it).
- **A restore can no longer be left half-done.** The database and its key are now swapped in together. If Horus is stopped in the middle (a power cut, say), it finishes the restore at the next start instead of being left with a database its key cannot open.
- **Two backups made in the same second** no longer overwrite each other.

**Speed and load**
- **Less load on Plex and Emby.** The Activity page, `/api/v1/activity` and `/metrics` share one answer from your servers for up to five seconds, so several open browsers or a fast Prometheus scrape no longer each call them.
- **Fewer "database is locked" errors.** A request now waits up to 30 seconds for the database instead of 5 when a backup or the recorder is writing.
- Start-up work (checking the database layout, reading the key file) is done once instead of on every request.

**Fixes**
- **"Could not check for updates" now says why** when GitHub is limiting requests from your network, and that it works again within the hour.
- **Adding a server checks what you typed.** The address must start with `http://` or `https://`, and name, address and token have length limits. The page shows the reason instead of "HTTP 422".
- The hint under *Release address* no longer says Horus never installs anything; it describes the Update now button.

**Upgrading**
- One database change runs automatically at start (migration 12 adds a column for the kept peak). A backup made with this version cannot be restored into 0.4.7 or older.
- `/api/v1/bandwidth` with `interval=1m` returns one point per five minutes for data older than 30 days.

**For developers**
- The test suite can no longer reach the network. A few older tests ran the daily update check for real, so every run of the suite used up some of GitHub's 60 requests an hour for that address.
- The unused `pydantic-settings` dependency is gone, and `.env.example` lists only variables Horus reads.

## 0.4.7 (2026-11-19)

**Security**
- **Stronger password hashing.** Passwords are hashed with a setting current guidance lists (scrypt with about 32 MB of memory per sign-in instead of 16 MB, and six times the work for anyone guessing). Nothing to do: each person's stored hash is renewed the next time they sign in. Passwords can be at most 1024 characters, and at most two sign-ins are checked at the same moment (the rest wait a fraction of a second), so a burst of sign-in attempts cannot use up a small container's memory.
- **Only admins see IP addresses.** A normal user used to see the address every stream was played from. They, and their API keys, now see "LAN" or "WAN" only.
- **Without a login, Horus answers only to an address it knows.** While there is no account yet, or with `HORUS_AUTH_DISABLED`, Horus answers when opened by its IP address or `localhost`, and refuses any other name. This stops a web page on another site from reaching it through your browser by pointing a domain name at it. **If you run with `HORUS_AUTH_DISABLED` and open Horus by a name** (for example through a reverse proxy), set the new `HORUS_ALLOWED_HOSTS` to that name before you update (see [Configuration](docs/configuration.md#environment-variables)). Installs with a login are not affected.
- **The database file and the data folder are private** on Linux: readable only by the account Horus runs as. A backup job that reads the folder as another account (not root) needs to run as that account.

**Accounts**
- **Sign out everywhere else.** A new button on the Account page ends your sign-ins on every other browser and device and leaves the password alone. Admins get **Sign out everywhere** for each person under Settings > Users.

**Documentation**
- [Accounts and security](docs/accounts-and-security.md) now says plainly that an admin can point Horus at any address, and that a media server added with `http://` gets its token sent unencrypted.

## 0.4.6 (2026-11-19)

**Security**
- **Restoring a backup no longer unpacks it in memory.** A small zip can unpack to gigabytes; the database is now unpacked to disk a piece at a time, and an oversized key file in a backup is refused. The 2 GB limit on a backup is unchanged.
- **The community-style Proxmox install is boxed in.** `install/horus-install.sh` still runs Horus as root (the community-scripts rules require it), but systemd now lets the service write only below `/opt/horus`, gain no privileges and hold no special capabilities. New installs only; it has not been run on a real Proxmox host yet.

**How Horus is built and published**
- **The Docker image is built from exact, hash-checked dependencies** (`requirements.txt`) on base images pinned by digest, and CI tests against the same versions. The image's web build moved from Node 20, which is no longer supported, to Node 24.
- **Releases and images carry signed build provenance.** See [Checking a download](docs/install.md#checking-a-download).
- **Published releases can no longer be changed,** and version tags cannot be moved or deleted. Release notes now come straight from this changelog.

## 0.4.5 (2026-11-19)

**Fixes**
- **Normal users get the "Plays per month" chart.** It was refused for anyone who is not an admin, so the chart was silently missing for them. It shows only their own plays when "Normal users see only their own activity" is on.
- **A notification channel whose address can no longer be read no longer breaks things.** If the encryption key was lost or replaced (for example a database restored without its key), every poll that wanted to send a notification failed and Settings > Integrations showed an error. Such a channel is now skipped and marked on the page, with a note to remove it and add it again; the other channels keep working.

**Behind a reverse proxy**
- **Wrong passwords no longer lock everybody out.** Five wrong passwords used to block every sign-in from that address for a minute, and behind a reverse proxy every visitor shares the proxy's address. The count is now kept per username and address together, so only that username waits; an address that fails thirty times in a minute across any usernames still has to wait.
- **New `HORUS_TRUSTED_PROXIES`** (see [Configuration](docs/configuration.md#environment-variables)): name your reverse proxy's address or network and Horus logs and limits sign-ins by each visitor's own address. Not needed for a proxy on the same machine.
- **The session cookie is marked `Secure`** when the visitor used https through a proxy that sets `X-Forwarded-Proto`. No setting needed.

**Settings**
- The **Maintenance** entry in the Settings menu is now called **Maintenance / Updates**, so it is easier to find where updates live. The menu is a little wider to fit it; the page address is unchanged.

## 0.4.4 (2026-11-19)

**Security**
- **Changes are accepted only from Horus's own pages.** A page at another address, including another web app on the same machine at a different port, could send requests with the cookie of an admin who had Horus open: shut it down, restore a backup, or upload its own. Such requests are now refused. This also protects a copy that runs with the login turned off. Scripts and API keys are not affected.
- **Security headers on every response:** a content security policy (scripts, styles and requests only from Horus itself), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **Horus can no longer be shown inside another page (a frame).** If you embedded it in a dashboard such as Homarr or Organizr, use a link instead.
- **Posters can only be pictures.** The poster proxy passes on JPEG, PNG, WebP and GIF only (an SVG can carry scripts) and tells the browser to treat the result as a picture and nothing else.
- **The generated API pages are gone.** `/docs`, `/redoc` and `/openapi.json` were open without a login. The API is described in [docs/api.md](docs/api.md).

**For scripts that call the API**
- A request with a body must be sent as `Content-Type: application/json`, and a backup file for a restore as `application/octet-stream`; anything else gets HTTP 415. `scripts/horusctl.py` already does both.

## 0.4.3 (2026-11-19)

**Security**
- **Control keys can no longer reach the secrets.** A control key could download a full backup, which holds the encryption key, the server tokens and the password hashes, and could restore from an uploaded file, which replaces the accounts. Both now need a signed-in admin. A control key also can no longer change the release address. It can still make a backup and restore one already saved on that install. If you have given a control key to anything you do not fully trust, revoke it and consider signing in to your servers again so they get new tokens.
- **Update now installs only the official release.** The button used to accept a release file from any GitHub repository that the release address pointed at. It now installs only from the official Horus repository's releases, whatever the release address says, and only when the download matches the SHA-256 that GitHub reports for it. A release from anywhere else is still shown as available, to install by hand.
- The answer from the release address is checked before it is shown: the version must look like a version, and the release-notes link must be https.

**Documentation**
- The backup section said full backups were never served over HTTP and that Download gave a copy without tokens. That was wrong: **Download** gives the full backup (database plus key), which is how you move Horus to another machine. The docs now say so, and that it must be kept private.

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
