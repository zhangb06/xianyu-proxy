ALTER TABLE message ADD COLUMN raw_json TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS card (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_card_account ON card (account_id, id);

CREATE TABLE IF NOT EXISTS card_secret (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES card (id) ON DELETE CASCADE,
    secret TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    order_no TEXT NOT NULL DEFAULT '',
    claimed_at TEXT,
    used_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (card_id, secret)
);
CREATE INDEX IF NOT EXISTS idx_card_secret_status ON card_secret (card_id, status);

CREATE TABLE IF NOT EXISTS card_item (
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    card_id INTEGER NOT NULL REFERENCES card (id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (card_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_card_item_item ON card_item (account_id, item_id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    order_no TEXT NOT NULL,
    chat_id TEXT NOT NULL DEFAULT '',
    buyer_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'paid',
    paid_at TEXT,
    delivered_at TEXT,
    card_secret_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, order_no)
);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders (account_id, status, id);

CREATE TABLE IF NOT EXISTS delivery_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    item_id TEXT NOT NULL DEFAULT '',
    template TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, code)
);
CREATE INDEX IF NOT EXISTS idx_delivery_rule_account ON delivery_rule (account_id, enabled, priority);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    task TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    run_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_outbox_ready ON outbox (status, run_at);
CREATE INDEX IF NOT EXISTS idx_outbox_account ON outbox (account_id, status);
