from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 3


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StateStore:
    """Small SQLite state machine. Release files remain outside this database."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        with self.transaction() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
            row = connection.execute("SELECT version FROM schema_meta").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported indexer-state schema: {row['version']}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_resume ON runs(input_hash, config_hash, updated_at DESC);
                CREATE TABLE IF NOT EXISTS documents (
                    run_id TEXT NOT NULL, document_id TEXT NOT NULL, source_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL, status TEXT NOT NULL, canonical_path TEXT, canonical_sha256 TEXT,
                    PRIMARY KEY(run_id, document_id), FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS stages (
                    run_id TEXT NOT NULL, document_id TEXT NOT NULL, stage TEXT NOT NULL,
                    input_hash TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(run_id, document_id, stage),
                    FOREIGN KEY(run_id,document_id) REFERENCES documents(run_id,document_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
                    retryable INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id,document_id) REFERENCES documents(run_id,document_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, kind TEXT NOT NULL, provider TEXT,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    UNIQUE(run_id, document_id, kind, provider, sha256),
                    FOREIGN KEY(run_id,document_id) REFERENCES documents(run_id,document_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    document_id TEXT NOT NULL, kind TEXT NOT NULL, signature TEXT NOT NULL,
                    status TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT,
                    reviewer TEXT, reason TEXT, resolved_at TEXT,
                    UNIQUE(run_id, document_id, kind, signature),
                    FOREIGN KEY(run_id,document_id) REFERENCES documents(run_id,document_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS releases (
                    release_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS active_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT NOT NULL,
                    action TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(release_id) REFERENCES releases(release_id) ON DELETE RESTRICT
                );
                """
            )

    def find_or_create_run(self, run_id: str, input_hash: str, config_hash: str, now: str) -> str:
        row = self.connection.execute(
            "SELECT run_id FROM runs WHERE input_hash=? AND config_hash=? ORDER BY updated_at DESC LIMIT 1",
            (input_hash, config_hash),
        ).fetchone()
        if row:
            return str(row["run_id"])
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, 'running', ?, ?)",
                (run_id, input_hash, config_hash, now, now),
            )
        return run_id

    def upsert_document(self, run_id: str, document_id: str, source_path: str, source_hash: str, status: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO documents(run_id,document_id,source_path,source_hash,status)
                   VALUES(?,?,?,?,?) ON CONFLICT(run_id,document_id) DO UPDATE SET
                   source_path=excluded.source_path, source_hash=excluded.source_hash, status=excluded.status""",
                (run_id, document_id, source_path, source_hash, status),
            )

    def set_run_status(self, run_id: str, status: str, now: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=?", (status, now, run_id))

    def document(self, run_id: str, document_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM documents WHERE run_id=? AND document_id=?", (run_id, document_id)).fetchone()
        if not row:
            raise KeyError(f"unknown document {document_id}")
        return row

    def documents(self, run_id: str) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM documents WHERE run_id=? ORDER BY document_id", (run_id,)))

    def set_document_status(self, run_id: str, document_id: str, status: str, canonical_path: str | None = None, canonical_sha256: str | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE documents SET status=?, canonical_path=COALESCE(?, canonical_path), canonical_sha256=COALESCE(?, canonical_sha256) WHERE run_id=? AND document_id=?",
                (status, canonical_path, canonical_sha256, run_id, document_id),
            )

    def record_stage(self, run_id: str, document_id: str, stage: str, input_hash: str, status: str, detail: Any, now: str, retryable: bool = False) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO stages VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,stage) DO UPDATE SET
                   input_hash=excluded.input_hash,status=excluded.status,detail_json=excluded.detail_json,updated_at=excluded.updated_at""",
                (run_id, document_id, stage, input_hash, status, canonical_json(detail), now),
            )
            connection.execute(
                "INSERT INTO attempts(run_id,document_id,stage,status,retryable,message,created_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, document_id, stage, status, int(retryable), canonical_json(detail), now),
            )

    def record_artifact(self, run_id: str, document_id: str, kind: str, provider: str | None, path: str, sha256: str, metadata: Any) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(run_id,document_id,kind,provider,path,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
                (run_id, document_id, kind, provider, path, sha256, canonical_json(metadata)),
            )

    def artifact_hash(self, run_id: str, document_id: str, kind: str, provider: str | None) -> str:
        row = self.connection.execute("SELECT sha256 FROM artifacts WHERE run_id=? AND document_id=? AND kind=? AND provider IS ? ORDER BY artifact_id DESC LIMIT 1", (run_id, document_id, kind, provider)).fetchone()
        if not row:
            raise RuntimeError(f"missing stored {kind}/{provider} artifact")
        return str(row["sha256"])

    def open_review(self, run_id: str, document_id: str, kind: str, signature: str, before: Any) -> int:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO reviews(run_id,document_id,kind,signature,status,before_json) VALUES(?,?,?,?, 'open', ?)",
                (run_id, document_id, kind, signature, canonical_json(before)),
            )
            row = connection.execute(
                "SELECT review_id FROM reviews WHERE run_id=? AND document_id=? AND kind=? AND signature=?",
                (run_id, document_id, kind, signature),
            ).fetchone()
        return int(row["review_id"])

    def review(self, review_id: int) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown review {review_id}")
        return row

    def reviews(self, run_id: str, status: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM reviews WHERE run_id=?" + (" AND status=?" if status else "") + " ORDER BY review_id"
        return list(self.connection.execute(query, (run_id, status) if status else (run_id,)))

    def resolve_with_canonical(self, review_id: int, reviewer: str, reason: str, after: Any, canonical_path: str, canonical_sha256: str, facts: int, now: str) -> bool:
        """One transaction: resolution audit, artifact metadata, and approval only if no reviews remain."""
        with self.transaction() as connection:
            review = connection.execute("SELECT run_id,document_id FROM reviews WHERE review_id=? AND status='open'", (review_id,)).fetchone()
            if not review:
                raise RuntimeError("review is not open")
            run_id, document_id = str(review["run_id"]), str(review["document_id"])
            connection.execute("UPDATE reviews SET status='resolved', after_json=?, reviewer=?, reason=?, resolved_at=? WHERE review_id=?", (canonical_json(after), reviewer, reason, now, review_id))
            open_count = connection.execute("SELECT COUNT(*) FROM reviews WHERE run_id=? AND document_id=? AND status='open'", (run_id, document_id)).fetchone()[0]
            connection.execute("INSERT OR IGNORE INTO artifacts(run_id,document_id,kind,provider,path,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)", (run_id, document_id, "canonical", None, canonical_path, canonical_sha256, canonical_json({"facts": facts})))
            if open_count:
                return False
            connection.execute("UPDATE documents SET status='canonical_approved', canonical_path=?, canonical_sha256=? WHERE run_id=? AND document_id=?", (canonical_path, canonical_sha256, run_id, document_id))
            connection.execute("INSERT INTO stages(run_id,document_id,stage,input_hash,status,detail_json,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,stage) DO UPDATE SET input_hash=excluded.input_hash,status=excluded.status,detail_json=excluded.detail_json,updated_at=excluded.updated_at", (run_id, document_id, "canonical", canonical_sha256, "canonical_approved", canonical_json({"facts": facts}), now))
            return True

    def approve_canonical(self, run_id: str, document_id: str, canonical_path: str, canonical_sha256: str, facts: int, now: str) -> None:
        """Atomically attach an auto-agreed canonical artifact to its document."""
        with self.transaction() as connection:
            if connection.execute("SELECT COUNT(*) FROM reviews WHERE run_id=? AND document_id=? AND status='open'", (run_id, document_id)).fetchone()[0]:
                raise RuntimeError("open review blocks canonical approval")
            connection.execute("INSERT OR IGNORE INTO artifacts(run_id,document_id,kind,provider,path,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)", (run_id, document_id, "canonical", None, canonical_path, canonical_sha256, canonical_json({"facts": facts})))
            connection.execute("UPDATE documents SET status='canonical_approved',canonical_path=?,canonical_sha256=? WHERE run_id=? AND document_id=?", (canonical_path, canonical_sha256, run_id, document_id))
            connection.execute("INSERT INTO stages(run_id,document_id,stage,input_hash,status,detail_json,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,stage) DO UPDATE SET input_hash=excluded.input_hash,status=excluded.status,detail_json=excluded.detail_json,updated_at=excluded.updated_at", (run_id, document_id, "canonical", canonical_sha256, "canonical_approved", canonical_json({"facts": facts}), now))

    def unresolved_count(self, run_id: str, document_ids: list[str] | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM reviews WHERE run_id=? AND status='open'"
        values: list[Any] = [run_id]
        if document_ids:
            query += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
            values.extend(document_ids)
        return int(self.connection.execute(query, values).fetchone()["count"])

    def record_release(self, release_id: str, run_id: str, path: str, manifest_sha256: str, now: str, status: str = "published") -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO releases VALUES(?,?,?,?,?,?)", (release_id, run_id, status, path, manifest_sha256, now))

    def release(self, release_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM releases WHERE release_id=?", (release_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown release {release_id}")
        return row

    def record_activation(self, release_id: str, action: str, now: str) -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO active_history(release_id,action,created_at) VALUES(?,?,?)", (release_id, action, now))

    def status(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id is None:
            row = self.connection.execute("SELECT run_id FROM runs ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not row:
                return {"run": None, "documents": [], "reviews": []}
            run_id = str(row["run_id"])
        run = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return {
            "run": dict(run) if run else None,
            "documents": [dict(row) for row in self.documents(run_id)],
            "reviews": [dict(row) for row in self.reviews(run_id)],
            "releases": [dict(row) for row in self.connection.execute("SELECT * FROM releases WHERE run_id=? ORDER BY created_at", (run_id,))],
        }
