CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    peer_id TEXT NOT NULL DEFAULT '',
    peer_name TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    paused_until TEXT,
    last_message_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_reply_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_account ON chat (account_id, last_message_at);

CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    msg_id TEXT,
    dedup_key TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'in',
    kind TEXT NOT NULL DEFAULT 'text',
    sender_id TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    timestamp_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, msg_id),
    UNIQUE (account_id, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_message_chat ON message (account_id, chat_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS keyword_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    reply TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_keyword_rule_account ON keyword_rule (account_id, enabled, priority);

CREATE TABLE IF NOT EXISTS reply_config (
    account_id INTEGER PRIMARY KEY REFERENCES account (id) ON DELETE CASCADE,
    default_reply TEXT NOT NULL DEFAULT '',
    debounce_seconds INTEGER NOT NULL DEFAULT 0,
    reply_expiry_seconds INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS block_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, kind, value)
);
