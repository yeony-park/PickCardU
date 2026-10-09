"""Create or migrate the local chat schema without touching application data."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA_VERSION = 2
_V1_COLUMNS = {
    "conversations": {
        "id",
        "owner_hash",
        "client_conversation_id",
        "title",
        "created_at",
        "updated_at",
    },
    "turns": {
        "id",
        "conversation_id",
        "seq",
        "client_request_id",
        "request_json",
        "query",
        "state",
        "attempt_id",
        "standalone_query",
        "answer_json",
        "rewrite_usage_json",
        "error_json",
        "created_at",
        "completed_at",
        "lease_expires_at",
    },
}
_V2_COLUMNS = {
    "conversations": _V1_COLUMNS["conversations"] | {"survey_context_json"},
    "turns": _V1_COLUMNS["turns"] | {"execution_context_json"},
}


def _version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def _validate_schema(db: sqlite3.Connection, expected: dict[str, set[str]]) -> None:
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != set(expected):
        raise sqlite3.DatabaseError("invalid chat schema tables")
    for table, required_columns in expected.items():
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if columns != required_columns:
            raise sqlite3.DatabaseError(f"invalid chat schema columns: {table}")


def _create_schema(db: sqlite3.Connection) -> None:
    try:
        schema = Path(__file__).with_name("chat_schema.sql").read_text(encoding="utf-8")
    except OSError as error:
        raise sqlite3.DatabaseError("chat schema file is unavailable") from error
    for statement in schema.split(";"):
        if statement.strip():
            db.execute(statement)


def ensure_chat_schema(db: sqlite3.Connection) -> None:
    """Ensure v2; v0/v1 leave their transaction for the caller to finish."""
    version = _version(db)
    if version == _SCHEMA_VERSION:
        _validate_schema(db, _V2_COLUMNS)
        return
    if version not in (0, 1):
        raise sqlite3.DatabaseError("unsupported chat schema")

    db.execute("BEGIN IMMEDIATE")
    version = _version(db)
    if version == _SCHEMA_VERSION:
        _validate_schema(db, _V2_COLUMNS)
        return
    if version == 0:
        if db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' LIMIT 1"
        ).fetchone():
            raise sqlite3.DatabaseError("unknown existing database")
        _create_schema(db)
    elif version == 1:
        _validate_schema(db, _V1_COLUMNS)
        db.execute("ALTER TABLE conversations ADD COLUMN survey_context_json TEXT")
        db.execute("ALTER TABLE turns ADD COLUMN execution_context_json TEXT")
        db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
    else:
        raise sqlite3.DatabaseError("unsupported chat schema")

    if _version(db) != _SCHEMA_VERSION:
        raise sqlite3.DatabaseError("chat schema migration did not set version 2")
    _validate_schema(db, _V2_COLUMNS)
