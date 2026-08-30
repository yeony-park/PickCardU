from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1000
SESSION_TTL = timedelta(hours=12)
SCRYPT = {"n": 1 << 14, "r": 8, "p": 1, "dklen": 32}

SCHEMA = """
BEGIN;
CREATE TABLE IF NOT EXISTS users(
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_salt BLOB NOT NULL,
 password_hash BLOB NOT NULL, scrypt_n INTEGER NOT NULL, scrypt_r INTEGER NOT NULL,
 scrypt_p INTEGER NOT NULL, scrypt_dklen INTEGER NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','developer')), active INTEGER NOT NULL CHECK(active IN (0,1)),
 onboarding_skipped_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 data_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(id,user_id)
);
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
 metadata_json TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(conversation_id,user_id) REFERENCES conversations(id,user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 conversation_id TEXT, run_kind TEXT NOT NULL CHECK(run_kind IN ('user_chat','developer_lab')),
 original_query TEXT NOT NULL, standalone_query TEXT, rewrite_status TEXT,
 status TEXT NOT NULL, answer_status TEXT, answer TEXT,
 config_json TEXT NOT NULL, config_hash TEXT NOT NULL, result_json TEXT,
 trace_json TEXT, usage_json TEXT, error_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(conversation_id,user_id) REFERENCES conversations(id,user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS runs_owner_kind ON runs(user_id,run_kind,created_at DESC);
COMMIT;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise OSError("refusing non-regular service database")
    connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    os.chmod(path, 0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and stat.S_ISREG(sidecar.lstat().st_mode):
            os.chmod(sidecar, 0o600)
    return connection


def init(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _hash_password(password: str, minimum: int) -> dict[str, Any]:
    if not isinstance(password, str) or not minimum <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(f"password length must be between {minimum} and {PASSWORD_MAX_LENGTH}")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return {"password_salt": salt, "password_hash": digest, **{f"scrypt_{key}": value for key, value in SCRYPT.items()}}


def hash_password(password: str) -> dict[str, Any]:
    return _hash_password(password, PASSWORD_MIN_LENGTH)


def verify_password(record: Mapping[str, Any], password: str) -> bool:
    try:
        digest = hashlib.scrypt(password.encode(), salt=record["password_salt"], n=int(record["scrypt_n"]), r=int(record["scrypt_r"]), p=int(record["scrypt_p"]), dklen=int(record["scrypt_dklen"]))
        return hmac.compare_digest(digest, record["password_hash"])
    except (KeyError, TypeError, ValueError):
        return False


def _row(row: sqlite3.Row | None, *json_fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        result[field.removesuffix("_json")] = _decode(result.pop(field))
    return result


def get_user(connection: sqlite3.Connection, *, user_id: str | None = None, username: str | None = None) -> dict[str, Any] | None:
    if (user_id is None) == (username is None):
        raise ValueError("provide one user key")
    field, value = ("id", user_id) if user_id else ("username", username)
    return _row(connection.execute(f"SELECT * FROM users WHERE {field}=?", (value,)).fetchone())


def _insert_user(connection: sqlite3.Connection, username: str, password: str, role: str, active: bool, minimum: int) -> dict[str, Any]:
    if not username.strip() or role not in {"user", "developer"}:
        raise ValueError("invalid seed account")
    record, timestamp, user_id = _hash_password(password, minimum), now(), new_id()
    connection.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, username.strip(), record["password_salt"], record["password_hash"], record["scrypt_n"], record["scrypt_r"], record["scrypt_p"], record["scrypt_dklen"], role, int(active), None, timestamp, timestamp),
    )
    return get_user(connection, user_id=user_id)  # type: ignore[return-value]


def create_user(connection: sqlite3.Connection, username: str, password: str, role: str = "user") -> dict[str, Any]:
    with transaction(connection):
        return _insert_user(connection, username, password, role, True, PASSWORD_MIN_LENGTH)


def seed_from_env(connection: sqlite3.Connection, environ: Mapping[str, str]) -> int:
    raw = environ.get("PICKCARDU_SEED_ACCOUNTS_JSON", "").strip()
    if not raw:
        return 0
    accounts = json.loads(raw)
    if not isinstance(accounts, list):
        raise ValueError("seed accounts must be a list")
    minimum = 1 if environ.get("PICKCARDU_ENV", "").strip().casefold() == "development" else PASSWORD_MIN_LENGTH
    inserted = 0
    with transaction(connection):
        for account in accounts:
            if not isinstance(account, dict) or not isinstance(account.get("username"), str) or not account["username"].strip() or not isinstance(account.get("password"), str) or not account["password"] or account.get("role", "user") not in {"user", "developer"} or not isinstance(account.get("active", True), bool):
                raise ValueError("invalid seed account")
            if get_user(connection, username=account.get("username")) is not None:
                continue
            _insert_user(connection, account["username"], account["password"], account.get("role", "user"), account.get("active", True), minimum)
            inserted += 1
    return inserted


def create_session(connection: sqlite3.Connection, user_id: str) -> str:
    token, timestamp = secrets.token_urlsafe(32), now()
    expires = (datetime.now(timezone.utc) + SESSION_TTL).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with transaction(connection):
        connection.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires, timestamp))
    return token


def get_session(connection: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    return _row(connection.execute("SELECT * FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone())


def delete_session(connection: sqlite3.Connection, token: str) -> bool:
    with transaction(connection):
        return connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).rowcount == 1


def public_user(user: Mapping[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in ("id", "username", "role", "active", "onboarding_skipped_at", "created_at", "updated_at")}


def get_profile(connection: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return None
    return {"user_id": row["user_id"], **json.loads(row["data_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}


def put_profile(connection: sqlite3.Connection, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    timestamp = now()
    with transaction(connection):
        connection.execute("INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at", (user_id, _json(data), timestamp, timestamp))
    return get_profile(connection, user_id)  # type: ignore[return-value]


def delete_profile(connection: sqlite3.Connection, user_id: str) -> bool:
    with transaction(connection):
        return connection.execute("DELETE FROM profiles WHERE user_id=?", (user_id,)).rowcount == 1


def skip_onboarding(connection: sqlite3.Connection, user_id: str) -> str:
    timestamp = now()
    with transaction(connection):
        connection.execute("UPDATE users SET onboarding_skipped_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, user_id))
    return timestamp


def create_conversation(connection: sqlite3.Connection, user_id: str, title: str | None) -> dict[str, Any]:
    conversation_id, timestamp = new_id(), now()
    with transaction(connection):
        connection.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", (conversation_id, user_id, title, timestamp, timestamp))
    return get_conversation(connection, user_id, conversation_id)  # type: ignore[return-value]


def get_conversation(connection: sqlite3.Connection, user_id: str, conversation_id: str) -> dict[str, Any] | None:
    return _row(connection.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)).fetchone())


def list_conversations(connection: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC,id DESC", (user_id,))]


def delete_conversation(connection: sqlite3.Connection, user_id: str, conversation_id: str) -> bool:
    with transaction(connection):
        return connection.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)).rowcount == 1


def _insert_message(connection: sqlite3.Connection, user_id: str, conversation_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    message_id, timestamp = new_id(), now()
    connection.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (message_id, conversation_id, user_id, role, content, _json(metadata) if metadata is not None else None, timestamp))
    connection.execute("UPDATE conversations SET updated_at=? WHERE id=? AND user_id=?", (timestamp, conversation_id, user_id))
    return {"id": message_id, "conversation_id": conversation_id, "user_id": user_id, "role": role, "content": content, "metadata": metadata, "created_at": timestamp}


def list_messages(connection: sqlite3.Connection, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
    return [{**dict(row), "metadata": _decode(row["metadata_json"])} for row in connection.execute("SELECT * FROM messages WHERE user_id=? AND conversation_id=? ORDER BY created_at,id", (user_id, conversation_id))]


def _run(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return _row(row, "config_json", "result_json", "trace_json", "usage_json", "error_json")


def get_run(connection: sqlite3.Connection, user_id: str, run_id: str) -> dict[str, Any] | None:
    return _run(connection.execute("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone())


def list_runs(connection: sqlite3.Connection, user_id: str, kind: str) -> list[dict[str, Any]]:
    return [_run(row) for row in connection.execute("SELECT * FROM runs WHERE user_id=? AND run_kind=? ORDER BY created_at DESC,id DESC", (user_id, kind))]  # type: ignore[misc]


def _insert_run(connection: sqlite3.Connection, user_id: str, query: str, kind: str, config: dict[str, Any], conversation_id: str | None = None) -> dict[str, Any]:
    run_id, timestamp, config_json = new_id(), now(), _json(config)
    connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, user_id, conversation_id, kind, query, None, None, "created", None, None, config_json, hashlib.sha256(config_json.encode()).hexdigest(), None, None, None, None, timestamp, timestamp))
    return get_run(connection, user_id, run_id)  # type: ignore[return-value]


def create_chat_request(connection: sqlite3.Connection, user_id: str, conversation_id: str, query: str, config: dict[str, Any]) -> dict[str, Any]:
    with transaction(connection):
        if get_conversation(connection, user_id, conversation_id) is None:
            raise LookupError("conversation not found")
        message = _insert_message(connection, user_id, conversation_id, "user", query)
        run = _insert_run(connection, user_id, query, "user_chat", config, conversation_id)
    return {"user_message": message, "run": run}


def create_lab_run(connection: sqlite3.Connection, user_id: str, query: str, config: dict[str, Any]) -> dict[str, Any]:
    with transaction(connection):
        return _insert_run(connection, user_id, query, "developer_lab", config)


def update_run(connection: sqlite3.Connection, user_id: str, run_id: str, **values: Any) -> dict[str, Any]:
    json_fields = {"config", "result", "trace", "usage", "error"}
    allowed = json_fields | {"standalone_query", "rewrite_status", "status", "answer_status", "answer"}
    if not values or set(values) - allowed:
        raise ValueError("invalid run update")
    assignments, parameters = [], []
    for key, value in values.items():
        field = f"{key}_json" if key in json_fields else key
        assignments.append(f"{field}=?")
        parameters.append(_json(value) if key in json_fields and value is not None else value)
        if key == "config":
            assignments.append("config_hash=?")
            parameters.append(hashlib.sha256(parameters[-1].encode()).hexdigest())
    parameters.extend((now(), run_id, user_id))
    with transaction(connection):
        if connection.execute(f"UPDATE runs SET {','.join(assignments)},updated_at=? WHERE id=? AND user_id=?", parameters).rowcount != 1:
            raise LookupError("run not found")
    return get_run(connection, user_id, run_id)  # type: ignore[return-value]


def complete_chat(connection: sqlite3.Connection, user_id: str, conversation_id: str, run_id: str, answer: str, result: dict[str, Any], trace: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    with transaction(connection):
        run = get_run(connection, user_id, run_id)
        if run is None or run["conversation_id"] != conversation_id:
            raise LookupError("run not found")
        assistant = _insert_message(connection, user_id, conversation_id, "assistant", answer, {"run_id": run_id, "citations_semantically_verified": False})
        connection.execute("UPDATE runs SET status='completed',answer_status='grounded_structural_validation',answer=?,result_json=?,trace_json=?,usage_json=?,error_json=NULL,updated_at=? WHERE id=? AND user_id=?", (answer, _json(result), _json(trace), _json(usage), now(), run_id, user_id))
    return assistant


def fail_run(connection: sqlite3.Connection, user_id: str, run_id: str, error: dict[str, Any], *, result: dict[str, Any] | None = None, trace: dict[str, Any] | None = None, usage: dict[str, Any] | None = None, diagnostic: str | None = None) -> dict[str, Any]:
    with transaction(connection):
        run = get_run(connection, user_id, run_id)
        if run is None:
            raise LookupError("run not found")
        connection.execute("UPDATE runs SET status='failed',answer_status=?,result_json=?,trace_json=?,usage_json=?,error_json=?,updated_at=? WHERE id=? AND user_id=?", (error["code"].casefold(), _json(result) if result else None, _json(trace) if trace else None, _json(usage) if usage else None, _json(error), now(), run_id, user_id))
        if diagnostic and run["conversation_id"]:
            _insert_message(connection, user_id, run["conversation_id"], "assistant", diagnostic, {"run_id": run_id, "result_status": "retrieval_only"})
    return get_run(connection, user_id, run_id)  # type: ignore[return-value]


def complete_lab(connection: sqlite3.Connection, user_id: str, run_id: str, result: dict[str, Any], trace: dict[str, Any], usage: dict[str, Any], answer: str | None) -> dict[str, Any]:
    with transaction(connection):
        if get_run(connection, user_id, run_id) is None:
            raise LookupError("run not found")
        connection.execute("UPDATE runs SET status='completed',answer_status=?,answer=?,result_json=?,trace_json=?,usage_json=?,error_json=NULL,updated_at=? WHERE id=? AND user_id=?", ("completed" if answer else "not_requested", answer, _json(result), _json(trace), _json(usage), now(), run_id, user_id))
    return get_run(connection, user_id, run_id)  # type: ignore[return-value]


def delete_run(connection: sqlite3.Connection, user_id: str, run_id: str) -> bool:
    with transaction(connection):
        return connection.execute("DELETE FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).rowcount == 1


def lab_counts(connection: sqlite3.Connection, user_id: str, config_hash: str) -> dict[str, int]:
    row = connection.execute("SELECT COUNT(*) total,COUNT(DISTINCT config_hash) unique_count,SUM(config_hash=?) seen FROM runs WHERE user_id=? AND run_kind='developer_lab' AND status='completed'", (config_hash, user_id)).fetchone()
    return {"total_run_count": int(row["total"]), "unique_config_count": int(row["unique_count"]), "config_seen_count": int(row["seen"] or 0)}
