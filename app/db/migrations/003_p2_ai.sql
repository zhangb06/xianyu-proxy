CREATE TABLE IF NOT EXISTS ai_config (
    account_id INTEGER PRIMARY KEY REFERENCES account (id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com',
    model TEXT NOT NULL DEFAULT 'deepseek-chat',
    api_key_enc TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    product_info TEXT NOT NULL DEFAULT '',
    price_floor_cents INTEGER NOT NULL DEFAULT 0,
    max_history INTEGER NOT NULL DEFAULT 10,
    timeout_seconds INTEGER NOT NULL DEFAULT 20,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_account ON ai_usage (account_id, created_at);
