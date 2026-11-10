CREATE TABLE IF NOT EXISTS item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '在售',
    cover_url TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_item_account_status ON item (account_id, status, id);

CREATE TABLE IF NOT EXISTS item_delivery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    mode TEXT NOT NULL DEFAULT 'card',
    card_id INTEGER REFERENCES card (id) ON DELETE SET NULL,
    template TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    auto_consign INTEGER NOT NULL DEFAULT 1,
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_item_delivery_account ON item_delivery (account_id, enabled);
