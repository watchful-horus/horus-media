# Servers, history and integrations

## Adding servers
- **Plex:** use *Sign in with Plex* in Settings > Servers: Horus opens plex.tv in a new tab, you approve, then pick a server. Horus contacts plex.tv only for this step. Or paste an `X-Plex-Token` manually.
- **Emby:** create an API key in the Emby dashboard (Advanced > API Keys) and paste it in.

Use the **Test connection** button before saving. A server can be renamed later; its history follows.

## Importing past history
Horus records plays from the moment it starts watching a server. To bring in older plays, open **Settings > Servers** and press **Import history** on a server.

**Plex.** Nothing to set up: Plex hands over everything in its own history.

**Emby.** Emby does not keep a play history of its own, so Horus reads it from the **Playback Reporting** plugin:
1. In Emby open **Dashboard > Plugins > Catalogue**, find **Playback Reporting** and install it.
2. Restart Emby when it asks.
3. Make sure the API key you gave Horus is an administrator's (create one under **Dashboard > API Keys**).
4. Press **Import history** in Horus.

The plugin only has plays from the day it was installed, so a new install has little or nothing to import. If it was installed long ago, you get all of that history.

If the import says the plugin was not found, Emby answered "not found" (usually the plugin is not installed, or Emby was not restarted after installing it) or refused the key (it is not an administrator's). Turn the log level to DEBUG under **Settings > Logs** and try again: the log shows the status Emby answered (404 means the plugin is missing, 401 or 403 means the key is not an administrator's).

**Import once, right after you add the server.** Importing only reads from the server and changes nothing on it. Running it again adds nothing new: each play has a stable key, so one already imported is skipped. Plays Horus recorded live are skipped too, matched by user, title and start time (within two minutes). Plex names a play the same way in both places, so those match. Emby's plugin may name a play differently from how Horus saw it live, so on Emby the longer Horus has been recording before you import, the likelier a few plays count twice. A play for which Emby's plugin gives only a date (no time) is placed at 12:00 midday in your Horus time zone, so it lands on the right day. Imported plays have no bandwidth figure, and Plex does not say how a past play was streamed, so those show as "Unknown" in the direct play vs transcode charts.

## Notifications and alerts
**Settings > Integrations** can post a message to a Discord channel webhook, an ntfy topic, or any web address (JSON) when someone starts or finishes playing, or a server becomes unreachable or comes back. Channel addresses are secrets: they are stored encrypted and never shown again. Tick the matching event on a channel to receive it.

Three optional alerts (**Settings > Alerts and reports**, all off by default) use the same channels: too many transcodes at once, a server's bandwidth over a limit, and someone Horus has never seen starting to play. Each is sent once when the limit is crossed.

## Summary reports
The same section can send a weekly or monthly summary report. **Integrations > Report** shows one on screen, downloads it as CSV, or sends it now.

## Seerr
Connecting Overseerr or Jellyseerr adds a **Requests** tab listing recent media requests. Horus only reads from Seerr.
