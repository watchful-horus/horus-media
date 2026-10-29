# API

## Data API for Grafana and other tools
Create an API key in **Settings > API** (or under **Account**), then read your data over HTTP. Send the key as `Authorization: Bearer <key>` or `X-API-Key: <key>`. A key can only use the read-only `/api/v1` endpoints; it cannot change anything, add servers or see server tokens or addresses. Every endpoint returns a plain JSON array (or CSV with `format=csv`).

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/plays?from&to&interval=hour\|day\|week\|month&by=none\|server\|user\|platform\|play_method\|media_type` | Plays per time bucket |
| `GET /api/v1/bandwidth?from&to&interval=auto\|1m\|5m\|15m\|1h\|1d` | Average and peak kbps per server, plus a combined `Total` |
| `GET /api/v1/history?from&to&server&user&limit&offset` | Individual plays, newest first |
| `GET /api/v1/activity` | What is playing now, one row per stream |
| `GET /api/v1/servers` | Server names and types |

`from` and `to` take dates, ISO date-times, or epoch milliseconds (Grafana's `${__from}` and `${__to}`). Days and hours follow the time zone in Settings.

In Grafana, install the **Infinity** data source, add it with *Bearer Token* authentication and your key, and point a JSON panel at one of the addresses above, for example `http://horus:8000/api/v1/plays?from=${__from}&to=${__to}&interval=day&by=server`. Set `time` as a Timestamp column and use the Time series format to get one line per server. Grafana makes the requests from wherever it runs, so Horus must be reachable from there (see [Port, network access](configuration.md#port-network-access-restart-and-shut-down)).

## Prometheus metrics
`GET /metrics` returns Prometheus text: whether each server is up, streams by play method, combined stream bandwidth, and plays recorded. Only server names appear in labels. It needs an admin's API key (Settings > API), sent with Prometheus's `authorization` setting:

```yaml
scrape_configs:
  - job_name: horus
    authorization: { credentials: hk_your_key }
    static_configs: [{ targets: ["horus:8000"] }]
```

## The app's own API
These are used by the web UI and need a signed-in session (not an ordinary API key).

- `POST /api/servers` add `{type: plex|emby, name, url, token}`; `GET /api/servers`; `DELETE /api/servers/{id}`
- `POST /api/plex/link`, `GET /api/plex/link/{pin}`, `POST /api/plex/link/{pin}/add` the Sign in with Plex flow (the account token never leaves the server)
- `POST /api/servers/{id}/test` test a saved server; `POST /api/servers/test` test `{type, url, token}` without saving
- `GET /api/activity` live sessions from all servers, merged, plus per-server status
- `POST /api/activity/stop` end a stream (signed-in admin only)
- `GET /api/stats?start=YYYY-MM-DD&end=YYYY-MM-DD` aggregated history for the graphs
- Tokens are never returned by the API.

## Control keys (for testing and automation)
An ordinary API key can only read the data endpoints under `/api/v1`. A **control key** can also run Horus: make and restore backups, change settings, add servers, import history, read the log, restart and shut down. It acts as the admin who made it, with these limits:

- **Off by default.** An admin switches control keys on under **Settings > API > Control keys**. Switching them off stops every control key at once.
- **Always expires.** Choose 24 hours, 7, 30 or 90 days when you make one.
- **Never users, keys, sign-in or Stop stream.** A control key cannot create or delete people, create or revoke keys, change passwords, sign in or stop a stream, and cannot switch control keys on or off.
- **Logged.** Every use is written to the log with the key's name, never the key itself.
- A control key stops working if its owner is no longer an admin.

Send it as `Authorization: Bearer hk_...` (or `X-API-Key`). `scripts/horusctl.py` is a small command-line helper that reads the address and key from a git-ignored `.env` file (`HORUS_TEST_URL`, `HORUS_TEST_CONTROL_KEY`) and never prints the key, for example `python scripts/horusctl.py GET /api/settings`. Only give a control key to something you trust, and use it on a network you trust or over HTTPS: a key sent over plain http can be read by anyone on the same network.
