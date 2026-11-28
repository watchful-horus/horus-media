# Servers, history and integrations

## Adding servers
- **Plex:** use *Sign in with Plex* in Settings > Servers: Horus opens plex.tv in a new tab, you approve, then pick a server. Horus contacts plex.tv only for this step. Or paste an `X-Plex-Token` manually.
- **Emby:** create an API key in the Emby dashboard (Advanced > API Keys) and paste it in.
- **Jellyfin:** create an API key in the Jellyfin dashboard (**Dashboard > API Keys**) and paste it in. **Test connection** checks the key too, so a wrong key is caught before you save.

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

**Jellyfin.** The same idea as Emby: Jellyfin's own **Playback Reporting** plugin holds the history. Install it under **Dashboard > Plugins > Catalog**, restart Jellyfin, then press **Import history**. Horus asks the plugin which days each person played something and reads those days one by one, so a long history takes a little while. The plugin gives a play's time to the minute, in the time zone Jellyfin itself uses, and Horus reads it as UTC, so imported Jellyfin plays can sit some hours off; a time it cannot read is treated like Emby's date-only plays below. Not yet tried against a real Jellyfin with the plugin installed: please report what you see.

**Import once, right after you add the server.** Importing only reads from the server and changes nothing on it. Running it again adds nothing new: each play has a stable key, so one already imported is skipped. Plays Horus recorded live are skipped too, matched by user, title and start time (within two minutes). Plex names a play the same way in both places, so those match. The Emby and Jellyfin plugins may name a play differently from how Horus saw it live, so on those servers the longer Horus has been recording before you import, the likelier a few plays count twice. A play for which Emby's plugin gives only a date (no time) is placed at 12:00 midday in your Horus time zone, so it lands on the right day. Imported plays have no bandwidth figure, and Plex does not say how a past play was streamed, so those show as "Unknown" in the direct play vs transcode charts.

## Notifications and alerts
**Settings > Notifications** can post a message to a Discord channel webhook, an ntfy topic, a Telegram chat, a Gotify server, Pushover, or any web address (JSON) when someone starts or finishes playing, a server becomes unreachable or comes back, or a stream has a [playback problem](#playback-problems). Channel addresses, tokens and keys are secrets: they are stored encrypted and never shown again. Tick the matching event on a channel to receive it.

| Type | What you enter |
|---|---|
| Discord, ntfy, web address | One address: the channel webhook, the full topic address, or your own URL |
| Telegram | The bot token from @BotFather and the chat ID to send to (a number, or `@channelname`). Start a chat with your bot first, or add it to the group or channel |
| Gotify | Your Gotify server's address and an app token (Apps in Gotify) |
| Pushover | The API token of an application you created at Pushover, and your user or group key |

Telegram messages are sent as plain text. Telegram, Gotify and Pushover were built from their published API descriptions and tested against stand-ins, not yet against the real services: press **Send test** after adding one and please report what happens.

**Edit** on a channel changes its name, type, address and events in place. The address field starts empty: leave it empty to keep the current address, or enter a new one to replace it (for Telegram, Gotify and Pushover the boxes work the same way: leave them all empty to keep what is stored). Changing the type needs a new address. Nothing is sent when you save; press **Send test** afterwards to check a new address. A channel whose address can no longer be read (the encryption key was lost or replaced) is repaired the same way, by entering its address again.

Three optional alerts (*Alerts and reports* on the same page, all off by default) use the same channels: too many transcodes at once, a server's bandwidth over a limit, and someone Horus has never seen starting to play. Each is sent once when the limit is crossed.

## Playback problems
While Horus records history it also watches every playing stream for trouble. It only reads what Plex, Emby and Jellyfin report, once per poll, so treat it as a good hint rather than a measurement.

| Problem | What it means | Plex | Emby |
|---|---|---|---|
| Buffering | The player itself says it is buffering | Yes | No (Emby has no such state; it shows as Stalled) |
| Stalled | Not paused, yet the position has not moved for 40 seconds | Yes | Yes |
| Slow transcode | The server converts the stream slower than it plays, and is not merely pausing because it is far enough ahead | Yes, from the speed Plex reports | Yes. Emby reports no speed, so Horus works it out from how far the conversion moved between two polls; it shows on the Activity page from the second poll of a stream |
| Direct play failed | The server had to convert because direct play gave an error | No | Yes |
| Keeps buffering | Three or more interruptions (buffering or stalled) in one play | Yes | Yes |

**Jellyfin** is read like Emby for Stalled, Direct play failed and Keeps buffering, and has no buffering state either. Slow transcode is not detected on Jellyfin: it reports a frame rate but not when a conversion is merely pausing because it is far enough ahead, so the two cannot be told apart.

A problem counts once it has been seen on two polls in a row, so a seek or the first seconds of a play are ignored. Something shorter than the poll interval is missed, and a stream without a position (live TV) cannot be seen to stall.

- **Activity** marks the stream in red next to its title, and shows the transcode speed and (Emby and Jellyfin) the reason for transcoding.
- **Graphs** has the problems over time, by user and by player for the chosen dates, and a list of the latest ones.
- **Notifications**: tick *A stream has a playback problem* on a channel. It is sent once per kind for each play, and once more when a play keeps buffering.
- Problems are kept as long as history is and are in [`/api/v1/problems`](api.md#data-api-for-grafana-and-other-tools). Someone who may only see their own activity sees only their own problems.

## Summary reports
*Alerts and reports* under **Settings > Notifications** can send a weekly or monthly summary report. **Report**, at the bottom of that page, shows one on screen, downloads it as CSV, or sends it now.
