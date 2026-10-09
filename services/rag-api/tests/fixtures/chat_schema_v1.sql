CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner_hash TEXT NOT NULL,
    client_conversation_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '새 채팅',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_hash, client_conversation_id)
);

CREATE INDEX IF NOT EXISTS conversations_owner_updated_idx
    ON conversations (owner_hash, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK (seq >= 1),
    client_request_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    query TEXT NOT NULL CHECK (length(query) BETWEEN 1 AND 500),
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'failed')),
    attempt_id TEXT NOT NULL,
    standalone_query TEXT,
    answer_json TEXT,
    rewrite_usage_json TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    lease_expires_at REAL NOT NULL,
    UNIQUE (conversation_id, seq),
    UNIQUE (conversation_id, client_request_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS turns_one_pending_per_conversation_idx
    ON turns (conversation_id)
    WHERE state = 'pending';

PRAGMA user_version = 1;
