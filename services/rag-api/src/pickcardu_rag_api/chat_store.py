"""Local chat storage; construction is lazy and never touches the RAG index."""
from __future__ import annotations

import base64
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ChatStoreError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status_code, self.code, self.message, self.retryable = status_code, code, message, retryable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _turn(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ('request', 'answer', 'rewrite_usage', 'error'):
        raw = result.pop(key + '_json')
        result[key] = json.loads(raw) if raw else None
    return result


class ChatStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connection(self, *, write=False):
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=5000')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise sqlite3.DatabaseError('unsupported chat schema')
            if version == 0:
                db.execute('BEGIN IMMEDIATE')
                # Recheck under the lock: two first requests may initialize together.
                if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise sqlite3.DatabaseError('unknown existing database')
                    schema = Path(__file__).with_name('chat_schema.sql').read_text(encoding='utf-8')
                    for statement in schema.split(';'):
                        if statement.strip():
                            db.execute(statement)
                db.commit()
            db.execute('SELECT owner_hash, client_conversation_id FROM conversations LIMIT 0')
            db.execute('SELECT attempt_id, lease_expires_at FROM turns LIMIT 0')
            if db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except (sqlite3.Error, OSError, json.JSONDecodeError) as error:
            if db:
                db.rollback()
            raise ChatStoreError(503, 'CHAT_STORAGE_UNAVAILABLE', '대화 저장소를 사용할 수 없습니다.', True) from error
        except BaseException:
            if db:
                db.rollback()
            raise
        finally:
            if db:
                db.close()

    @staticmethod
    def _owned(db, owner_hash, conversation_id):
        row = db.execute('SELECT * FROM conversations WHERE id=? AND owner_hash=?',
                         (conversation_id, owner_hash)).fetchone()
        if row is None:
            raise ChatStoreError(404, 'CONVERSATION_NOT_FOUND', '대화를 찾을 수 없습니다.')
        return row

    @staticmethod
    def _expire(db, conversation_id):
        if not db.execute("SELECT 1 FROM turns WHERE conversation_id=? AND state='pending' AND lease_expires_at<=?",
                          (conversation_id, time.time())).fetchone():
            return
        db.execute("UPDATE turns SET state='failed', error_json=?, completed_at=? "
                   "WHERE conversation_id=? AND state='pending' AND lease_expires_at<=?",
                   (_json({'code': 'TURN_INTERRUPTED', 'message': '답변 처리가 중단됐습니다. 다시 시도할 수 있습니다.',
                           'retryable': True, 'request_id': 'interrupted'}), _now(), conversation_id, time.time()))

    def create_conversation(self, owner_hash: str, client_conversation_id: str) -> dict:
        with self._connection(write=True) as db:
            existing = db.execute('SELECT * FROM conversations WHERE owner_hash=? AND client_conversation_id=?',
                                  (owner_hash, client_conversation_id)).fetchone()
            created = existing is None
            if created:
                cid, now = str(uuid.uuid4()), _now()
                db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)',
                           (cid, owner_hash, client_conversation_id, '새 채팅', now, now))
                existing = self._owned(db, owner_hash, cid)
            return {**dict(existing), '_created': created}

    def get_conversation(self, owner_hash: str, conversation_id: str) -> dict:
        with self._connection() as db:
            return dict(self._owned(db, owner_hash, conversation_id))

    def delete_conversation(self, owner_hash: str, conversation_id: str) -> None:
        with self._connection(write=True) as db:
            self._owned(db, owner_hash, conversation_id)
            if db.execute("SELECT 1 FROM turns WHERE conversation_id=? AND state='pending'", (conversation_id,)).fetchone():
                raise ChatStoreError(409, 'CONVERSATION_BUSY', '답변 생성 중인 대화는 삭제할 수 없습니다.')
            db.execute('DELETE FROM conversations WHERE id=? AND owner_hash=?', (conversation_id, owner_hash))

    def list_conversations(self, owner_hash: str, *, limit: int = 40, cursor: str | None = None) -> dict:
        boundary = None
        if cursor:
            try:
                boundary = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if not isinstance(boundary, list) or len(boundary) != 2 or not all(isinstance(x, str) for x in boundary):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise ChatStoreError(422, 'INVALID_REQUEST', '대화 목록 커서가 올바르지 않습니다.') from None
        with self._connection() as db:
            query = 'SELECT c.* FROM conversations c WHERE owner_hash=? AND EXISTS(SELECT 1 FROM turns t WHERE t.conversation_id=c.id)'
            params = [owner_hash]
            if boundary:
                query += ' AND (updated_at,id)<(?,?)'
                params.extend(boundary)
            rows = db.execute(query + ' ORDER BY updated_at DESC,id DESC LIMIT ?', [*params, limit+1]).fetchall()
            page = rows[:limit]
            next_cursor = base64.urlsafe_b64encode(_json([page[-1]['updated_at'], page[-1]['id']]).encode()).decode() if len(rows) > limit else None
            return {'conversations': [dict(row) for row in page], 'next_cursor': next_cursor}

    def reserve_turn(self, owner_hash: str, conversation_id: str, client_request_id: str,
                     request: dict, *, retry_failed: bool = False) -> dict:
        serialized = _json(request)
        with self._connection(write=True) as db:
            self._owned(db, owner_hash, conversation_id)
            self._expire(db, conversation_id)
            existing = db.execute('SELECT * FROM turns WHERE conversation_id=? AND client_request_id=?',
                                  (conversation_id, client_request_id)).fetchone()
            if existing is not None:
                if existing['request_json'] != serialized:
                    raise ChatStoreError(409, 'REQUEST_ID_CONFLICT', '동일 요청 ID의 내용이 다릅니다.')
                if existing['state'] == 'pending':
                    raise ChatStoreError(409, 'TURN_IN_PROGRESS', '이 질문의 답변을 처리하고 있습니다.')
                if existing['state'] == 'completed' or not retry_failed:
                    return {'turn': _turn(existing), 'attempt_id': existing['attempt_id'], 'should_run': False}
            if db.execute("SELECT 1 FROM turns WHERE conversation_id=? AND state='pending'", (conversation_id,)).fetchone():
                raise ChatStoreError(409, 'CONVERSATION_BUSY', '이 대화의 다른 질문을 처리하고 있습니다.')
            attempt, now = str(uuid.uuid4()), _now()
            if existing is not None:
                tid = existing['id']
                db.execute("UPDATE turns SET state='pending',attempt_id=?,answer_json=NULL,error_json=NULL,"
                           'rewrite_usage_json=NULL,standalone_query=NULL,completed_at=NULL,lease_expires_at=? WHERE id=?',
                           (attempt, time.time()+600, tid))
            else:
                seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM turns WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
                tid = str(uuid.uuid4())
                db.execute('INSERT INTO turns(id,conversation_id,seq,client_request_id,request_json,query,state,attempt_id,created_at,lease_expires_at) '
                           "VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                           (tid, conversation_id, seq, client_request_id, serialized, request['query'], attempt, now, time.time()+600))
                if seq == 1:
                    db.execute('UPDATE conversations SET title=? WHERE id=?', (request['query'][:32], conversation_id))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conversation_id))
            return {'turn': _turn(db.execute('SELECT * FROM turns WHERE id=?', (tid,)).fetchone()),
                    'attempt_id': attempt, 'should_run': True}

    def _finish(self, turn_id, attempt_id, *, answer=None, error=None, standalone_query=None, rewrite_usage=None):
        with self._connection(write=True) as db:
            row = db.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone()
            if row:
                self._expire(db, row['conversation_id'])
            now = _now()
            changed = db.execute('UPDATE turns SET state=?,answer_json=?,error_json=?,standalone_query=?,rewrite_usage_json=?,completed_at=? '
                                 "WHERE id=? AND attempt_id=? AND state='pending'",
                                 ('completed' if answer is not None else 'failed', _json(answer) if answer is not None else None,
                                  _json(error) if error is not None else None, standalone_query,
                                  _json(rewrite_usage) if rewrite_usage is not None else None, now, turn_id, attempt_id)).rowcount
            if not changed:
                raise ChatStoreError(409, 'TURN_ATTEMPT_STALE', '이 답변 처리 시도는 더 이상 유효하지 않습니다.')
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, row['conversation_id']))
            return _turn(db.execute('SELECT * FROM turns WHERE id=?', (turn_id,)).fetchone())

    def complete_turn(self, turn_id: str, attempt_id: str, answer: dict, *, standalone_query: str, rewrite_usage: dict | None = None) -> dict:
        return self._finish(turn_id, attempt_id, answer=answer, standalone_query=standalone_query, rewrite_usage=rewrite_usage)

    def fail_turn(self, turn_id: str, attempt_id: str, error: dict, *, rewrite_usage: dict | None = None) -> dict:
        return self._finish(turn_id, attempt_id, error=error, rewrite_usage=rewrite_usage)

    def list_turns(self, owner_hash: str, conversation_id: str, *, limit: int = 50, before_seq: int | None = None) -> dict:
        with self._connection() as db:
            self._owned(db, owner_hash, conversation_id)
            self._expire(db, conversation_id)
            rows = db.execute('SELECT * FROM turns WHERE conversation_id=? AND seq<? ORDER BY seq DESC LIMIT ?',
                              (conversation_id, before_seq if before_seq is not None else 9223372036854775807, limit+1)).fetchall()
            page = rows[:limit]
            pending = db.execute("SELECT 1 FROM turns WHERE conversation_id=? AND state='pending'", (conversation_id,)).fetchone() is not None
            return {'turns': [_turn(row) for row in reversed(page)],
                    'next_before_seq': page[-1]['seq'] if len(rows) > limit else None, 'has_pending': pending}

    def completed_turns(self, owner_hash: str, conversation_id: str, *, before_seq: int, limit: int = 2,
                        include_insufficient: bool = False) -> list[dict]:
        with self._connection() as db:
            self._owned(db, owner_hash, conversation_id)
            status_filter = "" if include_insufficient else "AND json_extract(answer_json,'$.answer_status')='answered' "
            rows = db.execute("SELECT * FROM turns WHERE conversation_id=? AND seq<? AND state='completed' "
                              + status_filter + "ORDER BY seq DESC LIMIT ?",
                              (conversation_id, before_seq, limit)).fetchall()
            return [_turn(row) for row in reversed(rows)]
