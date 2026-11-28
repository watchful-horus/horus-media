# Numbered SQL steps; index + 1 is the schema version. Append only, never edit applied steps.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK (type IN ('plex', 'emby')),
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        token TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        session_key TEXT NOT NULL,
        user TEXT NOT NULL,
        title TEXT NOT NULL,
        media_type TEXT NOT NULL,
        device TEXT NOT NULL,
        platform TEXT NOT NULL,
        play_method TEXT NOT NULL,
        bandwidth_kbps INTEGER,
        progress_percent REAL,
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ended_at TEXT
    );
    CREATE INDEX idx_history_started ON history (started_at);
    CREATE INDEX idx_history_open ON history (server_id, ended_at);
    CREATE TABLE bandwidth_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        ts TEXT NOT NULL,
        kbps INTEGER NOT NULL,
        sessions INTEGER NOT NULL
    );
    CREATE INDEX idx_samples_ts ON bandwidth_samples (ts);
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE admin (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT
    );
    """,
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    INSERT INTO users (username, password_hash, role, created_at)
        SELECT 'admin', password_hash, 'admin', created_at FROM admin;
    DROP TABLE admin;
    DROP TABLE sessions;
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    # Past plays brought in from Plex/Emby are marked, and can't be imported twice.
    """
    ALTER TABLE history ADD COLUMN imported INTEGER NOT NULL DEFAULT 0;
    CREATE UNIQUE INDEX idx_history_imported ON history (server_id, session_key) WHERE imported = 1;
    """,
    # Link a Horus login to a Plex/Emby name; per-user API keys.
    """
    ALTER TABLE users ADD COLUMN media_username TEXT NOT NULL DEFAULT '';
    ALTER TABLE api_keys ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
    """,
    # Notification channels (the address is encrypted: webhook addresses are secrets) and the
    # Seerr connection.
    """
    CREATE TABLE notification_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('webhook', 'discord', 'ntfy')),
        url TEXT NOT NULL,
        events TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE seerr (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        url TEXT NOT NULL,
        api_key TEXT NOT NULL
    );
    """,
    # Control keys: a key can carry an admin's power (everything except users and keys) and then
    # always expires. Ordinary keys stay scope 'read' with no expiry.
    """
    ALTER TABLE api_keys ADD COLUMN scope TEXT NOT NULL DEFAULT 'read';
    ALTER TABLE api_keys ADD COLUMN expires_at TEXT;
    """,
    # The Seerr requests view was removed; forget any stored Seerr address and API key.
    """
    DROP TABLE IF EXISTS seerr;
    """,
    # A friendly name people can choose for themselves (shown instead of the sign-in name).
    """
    ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
    """,
    # Bandwidth samples older than a month are thinned to one per five minutes, which then
    # holds the average in `kbps` and the highest sample in `peak_kbps`. Empty for a fresh
    # sample, whose peak is the sample itself.
    """
    ALTER TABLE bandwidth_samples ADD COLUMN peak_kbps INTEGER;
    """,
    # Playback problems the poller noticed (buffering, stalled, slow conversion). `location` is
    # only LAN or WAN, never an address. Removed with the history they belong to.
    """
    CREATE TABLE playback_problems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        session_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        user TEXT NOT NULL,
        title TEXT NOT NULL,
        device TEXT NOT NULL,
        platform TEXT NOT NULL,
        play_method TEXT NOT NULL,
        location TEXT,
        detail TEXT,
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ended_at TEXT
    );
    CREATE INDEX idx_problems_started ON playback_problems (started_at);
    """,
    # More kinds of notification channel (Telegram, Gotify, Pushover). The table listed the
    # allowed kinds itself, and SQLite cannot change that in place, so it is rebuilt without
    # the list: `notifications.KINDS` is what decides now. Ids and everything else are kept.
    """
    CREATE TABLE notification_channels_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        url TEXT NOT NULL,
        events TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    INSERT INTO notification_channels_new (id, name, kind, url, events, enabled, created_at)
        SELECT id, name, kind, url, events, enabled, created_at FROM notification_channels;
    DROP TABLE notification_channels;
    ALTER TABLE notification_channels_new RENAME TO notification_channels;
    """,
    # Jellyfin as a third kind of server. The servers table listed the allowed kinds itself, so
    # it is rebuilt without the list (`models.ServerType` decides). History, samples and
    # problems point at it; `db._migrate` switches the foreign-key rule off while it runs, so
    # dropping the old table does not take their rows with it. Ids are kept.
    """
    CREATE TABLE servers_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        token TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    INSERT INTO servers_new (id, type, name, url, token, created_at)
        SELECT id, type, name, url, token, created_at FROM servers;
    DROP TABLE servers;
    ALTER TABLE servers_new RENAME TO servers;
    """,
]
